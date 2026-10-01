"""reasoning.py — the ONLY file allowed to call an LLM provider (LiteLLM).

Repo rules honored here:
  #1  SCAFFOLD_TEAM_LLM_KEY pays for engine-internal reasoning only — never for a
      developer's own coding session (that goes through OpenCode's own routing).
  #2  Deterministic logic (diff parsing, SQL) never routes through the LLM — this
      module only *summarizes* or *answers*; it never decides what changed.
  #6  Context sent to the LLM is small and targeted — the caller builds the block.

Default provider is Google Gemini (free AI Studio tier); the model strings are
overridable via SCAFFOLD_LLM_MODEL / SCAFFOLD_EMBED_MODEL (engine/.env, optional).
"""

import json
import re
import time

from litellm import completion, embedding

from app.config import settings

CHAT_MODEL = "gemini/gemini-3.6-flash"
EMBED_MODEL = "gemini/gemini-embedding-001"
EMBED_DIM = 1536  # frozen: VECTOR(1536) in docs/SCHEMA.md / migrate_day3.sql


def chat_model() -> str:
    return (settings.scaffold_llm_model or "").strip() or CHAT_MODEL


def embed_model() -> str:
    return (settings.scaffold_embed_model or "").strip() or EMBED_MODEL


class LlmUnavailableError(RuntimeError):
    """Raised when the team LLM key is missing or the provider call fails."""


# Gemini free-tier flaps (503/429/timeout) were the demo's only known flaky beat.
_RETRYABLE_TOKENS = (
    "503", "502", "500", "504", "429", "service unavailable", "rate limit",
    "overloaded", "timeout", "timed out", "connection",
)
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


def _is_transient(exc: Exception) -> bool:
    """True for provider-side hiccups worth retrying; deterministic check (rule #3)."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in _RETRYABLE_STATUS:
        return True
    msg = str(exc).lower()
    return any(tok in msg for tok in _RETRYABLE_TOKENS)


def _completion_with_retry(**kwargs):
    """One model's completion call, retried on transient provider errors.

    Two retries (~1s/2s backoff) absorb the free-tier 503 flaps; anything
    non-transient re-raises immediately so callers keep their exact failure
    semantics (502 path unchanged).
    """
    delay = 1.0
    for attempt in range(3):
        try:
            return completion(**kwargs)
        except Exception as exc:
            if attempt == 2 or not _is_transient(exc):
                raise
            time.sleep(delay)
            delay *= 2


# Model fallback chain: the rotated SCAFFOLD_TEAM_LLM_KEY is a Google "new user"
# key, and the free-tier model roster for those keys flaps independently of the
# 503 retry above (whole models 404 "no longer available to new users", or the
# configured model's pool saturates so hard the 3 retries all fail). These are
# plain fallback candidates, not an LLM decision (repo rule #3) — the *first*
# model that answers wins and is cached for the rest of the process so later
# calls skip straight to it instead of re-probing every candidate every time.
FALLBACK_MODELS = [
    "gemini/gemini-3.1-flash-lite",
    "gemini/gemini-3.7-flash",
    "gemini/gemini-3.8-flash",
]

_model_cache: dict[str, str] = {}


def _candidate_models() -> list[str]:
    """Configured model first, then the fallback list, with any cached winner
    from an earlier call in this process moved to the front."""
    primary = chat_model()
    ordered = [primary] + [m for m in FALLBACK_MODELS if m != primary]
    winner = _model_cache.get("winner")
    if winner and winner in ordered:
        ordered.remove(winner)
        ordered.insert(0, winner)
    return ordered


def _is_model_unavailable(exc: Exception) -> bool:
    """True when the *model itself* is the problem (not a transient hiccup) —
    e.g. a 404 'no longer available to new users', so the next candidate should
    be tried instead of retrying the same dead model."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status == 404:
        return True
    msg = str(exc).lower()
    return "404" in msg or "not found" in msg or "no longer available" in msg


def _completion_with_fallback(**kwargs):
    """Try the configured model (with its own transient-error retries), then
    walk the fallback candidates on a 404/model-unavailable or on the primary's
    retries being fully exhausted by transient errors. Any other error type
    (bad key, etc.) still fails fast and propagates unchanged."""
    last_exc: Exception | None = None
    for model in _candidate_models():
        try:
            resp = _completion_with_retry(**{**kwargs, "model": model})
            _model_cache["winner"] = model
            return resp
        except Exception as exc:
            last_exc = exc
            if not (_is_model_unavailable(exc) or _is_transient(exc)):
                raise
    raise last_exc


def _key() -> str:
    key = (settings.scaffold_team_llm_key or "").strip()
    if not key:
        raise LlmUnavailableError(
            "SCAFFOLD_TEAM_LLM_KEY is not set — add your AI Studio key to engine/.env"
        )
    return key


def embed_text(text: str) -> list[float]:
    """Embed one string into the frozen 1536-dim space.

    gemini-embedding-001 is MRL-trained, so prefix-slicing to a lower dimension
    is valid; we slice defensively in case `dimensions` is not honored upstream.
    """
    key = _key()
    clipped = text[:8000]
    model = embed_model()
    try:
        res = embedding(model=model, input=[clipped], dimensions=EMBED_DIM, api_key=key)
    except Exception:  # older LiteLLM/provider rejects the `dimensions` kwarg
        res = embedding(model=model, input=[clipped], api_key=key)
    vec = list(res.data[0]["embedding"])
    if len(vec) > EMBED_DIM:
        vec = vec[:EMBED_DIM]
    if len(vec) != EMBED_DIM:
        raise LlmUnavailableError(f"embedding dimension {len(vec)} != expected {EMBED_DIM}")
    return vec


def summarize_diff(
    message: str,
    files_changed: list[str],
    routes_added: list[dict],
    dependencies_added: list[dict],
    env_keys_added: list[dict],
) -> str | None:
    """One-line LLM summary for commits.summary.

    The deterministic parser already DECIDED what changed (repo rule #2) — the
    LLM only phrases it. Raises on failure; callers fail open (summary -> null).
    """
    findings: list[str] = []
    if routes_added:
        findings.append(
            "routes: " + ", ".join(f"{r['method']} {r['route']}" for r in routes_added[:5])
        )
    if dependencies_added:
        findings.append("deps: " + ", ".join(d["name"] for d in dependencies_added[:8]))
    if env_keys_added:
        findings.append("env keys: " + ", ".join(e["key"] for e in env_keys_added[:8]))

    system = (
        "You write ONE-sentence summaries (max 20 words) of a git commit for a team "
        "dashboard. Plain text only — no quotes, no markdown, no lists."
    )
    user = (
        f"Commit message: {message}\n"
        f"Files: {', '.join(files_changed[:10])}\n"
        f"Detected changes: {'; '.join(findings) if findings else 'no structural changes detected'}"
    )
    resp = _completion_with_fallback(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        api_key=_key(),
        max_tokens=60,
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip().strip('"')
    line = text.splitlines()[0].strip() if text else ""
    return line[:200] or None


# ---------------------------------------------------------------------------
# Phase 4: recommendation explanations. The coordination service (pure, in
# services/coordination.py) DECIDES what to recommend and why (repo rule #3);
# the LLM here only phrases those database-grounded facts into one sentence.
# It receives no other context and cannot invent project state.
# ---------------------------------------------------------------------------

_EXPLAIN_SYSTEM = (
    "You are Scaffold, a coordination assistant. You are given a recommendation "
    "that was ALREADY decided by deterministic rules over real project data, "
    "plus the exact reasons for it. Restate it as ONE friendly sentence "
    "(max 40 words) that names the task and weaves in the reasons. Plain text "
    "only — no quotes, no markdown, no lists. Never mention any task, reason, "
    "person, or fact that is not in the input — never invent project state."
)


def explain_recommendation(task_title: str, reasons: list[str]) -> str | None:
    """One-sentence LLM phrasing of an already-made recommendation. Fail-open:
    returns None on any provider trouble and callers fall back to the
    deterministic reasons (the API works fully without an LLM)."""
    clean = [r.strip() for r in reasons if r and r.strip()][:6]
    user = f"RECOMMENDED TASK: {task_title}\nREASONS: " + "; ".join(clean)
    try:
        resp = _completion_with_retry(
            model=chat_model(),
            messages=[
                {"role": "system", "content": _EXPLAIN_SYSTEM},
                {"role": "user", "content": user},
            ],
            api_key=_key(),
            max_tokens=80,
            temperature=0.2,
        )
        text = (resp.choices[0].message.content or "").strip().strip('"')
        line = text.splitlines()[0].strip() if text else ""
        return line[:240] or None
    except Exception:
        return None


_ANSWER_SYSTEM = """You are Scaffold, the shared-awareness assistant for a small software team.
Answer the user's question using ONLY the project context provided. Be concise and concrete.
If the question implies new work, suggest up to 3 tasks.
Respond with ONLY a JSON object — no markdown fences, no prose — shaped exactly:
{"answer": "<your answer>", "suggested_tasks": [{"title": "<short imperative>", "owner_id": null, "due_at": null}]}
Assignment rule: if the context contains a TEAM ROSTER block, you MAY set owner_id — but ONLY
to a user_id copied verbatim from that roster. Never invent or truncate user IDs. Set due_at
only as an ISO-8601 date/datetime within the project deadline (if any) — otherwise null.
If there is no roster or no fitting teammate, use null."""


def answer_prompt(context_block: str, prompt: str) -> dict:
    """The /reason core: small targeted context + prompt -> {answer, suggested_tasks}.

    Day 5: the caller embeds the deterministic TEAM ROSTER in the context block,
    so suggestions may carry owner_ids — and the caller re-validates every one
    afterwards (availability.py). This function stays LLM-only and never decides
    what is valid (repo rule #2).
    """
    resp = _completion_with_fallback(
        messages=[
            {"role": "system", "content": _ANSWER_SYSTEM},
            {"role": "user", "content": f"PROJECT CONTEXT:\n{context_block}\n\nQUESTION:\n{prompt}"},
        ],
        api_key=_key(),
        # Gemini 3 thinking tokens count against this budget BEFORE the visible
        # JSON — 800 truncated responses mid-string (finish_reason=length ->
        # unparseable -> zero suggested_tasks). 2000 leaves headroom for both.
        max_tokens=2000,
        temperature=0.2,
    )
    raw = (resp.choices[0].message.content or "").strip()
    return _parse_answer(raw)


def _parse_answer(raw: str) -> dict:
    """Defensive parse of the model's JSON; degrades to the raw text answer."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"answer": raw.strip()[:2000], "suggested_tasks": []}
    if not (isinstance(data, dict) and isinstance(data.get("answer"), str)):
        return {"answer": raw.strip()[:2000], "suggested_tasks": []}

    tasks: list[dict] = []
    seen_titles: set[str] = set()
    for t in (data.get("suggested_tasks") or [])[:3]:
        if isinstance(t, dict) and isinstance(t.get("title"), str) and t["title"].strip():
            title = t["title"].strip()
            # Duplicates collide on the dashboard (the suggested-button key and
            # the click's removal filter are both title-based): keep the first.
            if title.lower() in seen_titles:
                continue
            seen_titles.add(title.lower())
            tasks.append(
                {
                    "title": title,
                    "owner_id": t.get("owner_id") if isinstance(t.get("owner_id"), str) else None,
                    "due_at": t.get("due_at") if isinstance(t.get("due_at"), str) else None,
                }
            )
    return {"answer": data["answer"].strip(), "suggested_tasks": tasks}
