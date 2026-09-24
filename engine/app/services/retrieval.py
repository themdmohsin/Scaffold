"""retrieval.py — targeted context retrieval (master doc §7).

Vector search over decisions/api_contracts embeddings (pgvector, cosine) with a
deterministic keyword/recency fallback, so /reason and the context summary
degrade instead of breaking when the embedding provider is down or a project
has no embeddings yet.

No LLM calls here except reasoning.embed_text for the query vector — scoring,
thresholds and ordering are plain SQL/Python (repo rule #3).
"""

import json
import math
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ApiContract, Decision
from app.services import reasoning

_TOP_K = 5          # rows fetched/kept per side
_MIN_COSINE = 0.35  # below this the vector hit is noise; fall back to keywords
_KW_POOL = 200      # keyword scan window (newest N rows)


# ---------------------------------------------------------------------------
# Embeddable text — what we store the embedding OF. Write paths + backfill use
# these so the vector always reflects the row it belongs to.
# ---------------------------------------------------------------------------

def embeddable_text_for_decision(text: str, reasoning_text: str | None) -> str:
    return f"{text}\n{reasoning_text or ''}".strip()


def embeddable_text_for_contract(
    method: str,
    route: str,
    request_schema: dict | None,
    response_schema: dict | None,
) -> str:
    parts = [f"{method} {route}"]
    for schema in (request_schema, response_schema):
        if schema:
            compact = json_compact(schema)[:600]
            if compact:
                parts.append(compact)
    return "\n".join(parts)


def json_compact(data: dict) -> str:
    try:
        return json.dumps(data, separators=(",", ":"), default=str)
    except Exception:
        return str(data)


def embed_decision_row(d: Decision) -> None:
    """Set d.embedding from its text; fail open to NULL on provider trouble.
    Used by POST /decisions, the webhook contract path, and the backfill script.
    """
    try:
        d.embedding = reasoning.embed_text(embeddable_text_for_decision(d.text, d.reasoning))
    except Exception:
        d.embedding = None


def embed_contract_row(c: ApiContract) -> None:
    try:
        c.embedding = reasoning.embed_text(
            embeddable_text_for_contract(c.method, c.route, c.request_schema, c.response_schema)
        )
    except Exception:
        c.embedding = None


# ---------------------------------------------------------------------------
# Deterministic helpers (repo rule #3): recency + keyword scoring
# ---------------------------------------------------------------------------

def recent_decisions(db: Session, project_id: uuid.UUID, limit: int = _TOP_K) -> list[dict]:
    rows = db.scalars(
        select(Decision)
        .where(Decision.project_id == project_id)
        .order_by(Decision.created_at.desc())
        .limit(limit)
    ).all()
    return [_decision_out(d) for d in rows]


def recent_contracts(db: Session, project_id: uuid.UUID, limit: int = _TOP_K) -> list[dict]:
    rows = db.scalars(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.created_at.desc())
        .limit(limit)
    ).all()
    return [_contract_out(c) for c in rows]


def _tokens(prompt: str) -> list[str]:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "for", "in", "on", "with",
        "is", "are", "we", "our", "us", "it", "this", "that", "how", "do",
        "does", "should", "can", "add", "new", "use", "using", "what", "which",
        "need", "want", "make", "get", "have", "has", "be", "at", "by", "from",
    }
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in prompt.lower()).split()]
    out: list[str] = []
    for w in words:
        if len(w) > 2 and w not in stop and w not in out:
            out.append(w)
    return out


def _kw_score(haystacks: list[str | None], tokens: list[str]) -> int:
    joined = " ".join(h.lower() for h in haystacks if h)
    return sum(1 for t in tokens if t in joined)


def keyword_decisions(db: Session, project_id: uuid.UUID, prompt: str, limit: int = _TOP_K) -> list[dict]:
    """Token-overlap scoring over the newest decisions; recency when nothing hits."""
    tokens = _tokens(prompt)
    rows = db.scalars(
        select(Decision)
        .where(Decision.project_id == project_id)
        .order_by(Decision.created_at.desc())
        .limit(_KW_POOL)
    ).all()
    if not tokens:
        return [_decision_out(d) for d in rows[:limit]]
    scored = [
        (_kw_score([d.text, d.reasoning], tokens), d)
        for d in rows
    ]
    hits = sorted((s for s in scored if s[0] > 0), key=lambda p: (p[0], p[1].created_at), reverse=True)
    chosen = [d for _, d in hits[:limit]] or rows[:limit]
    return [_decision_out(d) for d in chosen]


def keyword_contracts(db: Session, project_id: uuid.UUID, prompt: str, limit: int = _TOP_K) -> list[dict]:
    tokens = _tokens(prompt)
    rows = db.scalars(
        select(ApiContract)
        .where(ApiContract.project_id == project_id)
        .order_by(ApiContract.created_at.desc())
        .limit(_KW_POOL)
    ).all()
    if not tokens:
        return [_contract_out(c) for c in rows[:limit]]
    scored = [(_kw_score([c.method, c.route], tokens), c) for c in rows]
    hits = sorted((s for s in scored if s[0] > 0), key=lambda p: (p[0], p[1].created_at), reverse=True)
    chosen = [c for _, c in hits[:limit]] or rows[:limit]
    return [_contract_out(c) for c in chosen]


# ---------------------------------------------------------------------------
# Vector search with fallback
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _embed_query(prompt: str) -> list[float] | None:
    try:
        return reasoning.embed_text(prompt)
    except Exception:
        return None  # provider down / key missing -> keyword fallback


def relevant_decisions(db: Session, project_id: uuid.UUID, prompt: str, limit: int = _TOP_K) -> list[dict]:
    qvec = _embed_query(prompt)
    if qvec is not None:
        rows = db.scalars(
            select(Decision)
            .where(Decision.project_id == project_id, Decision.embedding.isnot(None))
            .order_by(Decision.embedding.cosine_distance(qvec))  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        hits = [d for d in rows if _cosine(list(d.embedding), qvec) >= _MIN_COSINE]
        if hits:
            return [_decision_out(d) for d in hits]
    return keyword_decisions(db, project_id, prompt, limit)


def relevant_contracts(db: Session, project_id: uuid.UUID, prompt: str, limit: int = _TOP_K) -> list[dict]:
    qvec = _embed_query(prompt)
    if qvec is not None:
        rows = db.scalars(
            select(ApiContract)
            .where(ApiContract.project_id == project_id, ApiContract.embedding.isnot(None))
            .order_by(ApiContract.embedding.cosine_distance(qvec))  # type: ignore[attr-defined]
            .limit(limit)
        ).all()
        hits = [c for c in rows if _cosine(list(c.embedding), qvec) >= _MIN_COSINE]
        if hits:
            return [_contract_out(c) for c in hits]
    return keyword_contracts(db, project_id, prompt, limit)


def retrieve_for_prompt(db: Session, project_id: uuid.UUID, prompt: str) -> dict:
    """One query embedding, both sides retrieved. Used by the /reason route."""
    return {
        "decisions": relevant_decisions(db, project_id, prompt),
        "contracts": relevant_contracts(db, project_id, prompt),
    }


# ---------------------------------------------------------------------------
# Output shapes (context contract + /reason prompt block)
# ---------------------------------------------------------------------------

def _decision_out(d: Decision) -> dict:
    return {
        "id": str(d.id),
        "text": d.text,
        "reasoning": d.reasoning,
        "created_at": d.created_at.isoformat(),
    }


def _contract_out(c: ApiContract) -> dict:
    return {
        "id": str(c.id),
        "route": c.route,
        "method": c.method,
        "request_schema": c.request_schema,
        "response_schema": c.response_schema,
        "created_at": c.created_at.isoformat(),
    }
