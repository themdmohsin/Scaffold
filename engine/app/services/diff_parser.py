"""Deterministic extraction from Git diffs — the source of truth for "what changed".

Repo rule #2: Git diffs decide what changed; an LLM may only summarize, never decide.
This module is pure functions: no I/O, no LLM, no DB. Input is unified-diff text
(as returned by GitHub's commit files[].patch), output is a structured summary.
"""

import re
from dataclasses import dataclass, field

# --- file section header: a new diff file starts here -------------------------
_DIFF_FILE_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_OLD_PATH_RE = re.compile(r"^--- (?:a/)?(?P<path>.+)$")
_NEW_PATH_RE = re.compile(r"^\+\+\+ (?:b/)?(?P<path>.+)$")

# --- route decorators / registrations, on ADDED lines -------------------------
_FASTAPI_RE = re.compile(
    r"""@(?:app|router|api)\.(get|post|put|patch|delete|head|options)\(\s*["'](?P<route>[^"']+)["']"""
)
_API_ROUTE_RE = re.compile(
    r"""@(?:app|router)\.api_route\(\s*["'](?P<route>[^"']+)["'].*?methods\s*=\s*\[(?P<methods>[^\]]*)\]"""
)
_EXPRESS_RE = re.compile(
    r"""\b(?:app|router)\.(get|post|put|patch|delete|all)\(\s*["'`](?P<route>[^"'`]+)["'`]"""
)
_FLASK_RE = re.compile(
    r"""@(?:app|bp|blueprint)\.route\(\s*["'](?P<route>[^"']+)["'](?:.*?methods\s*=\s*\[(?P<methods>[^\]]*)\])?""",
    re.DOTALL,
)

# --- dependencies, on ADDED lines in dependency files -------------------------
_REQUIREMENTS_RE = re.compile(r"^\+\s*(?P<name>[A-Za-z0-9][A-Za-z0-9_.\-\[\]]*)\s*(?P<spec>[<>=~!].*)?\s*$")
_PYPROJECT_RE = re.compile(r"""^\+\s*["'](?P<name>[A-Za-z0-9][A-Za-z0-9_.\-\[\]]*)\s*(?P<spec>[<>=~!^][^"']*)?["']""")
_PACKAGEJSON_RE = re.compile(r"""^\+\s*["'](?P<name>@?[A-Za-z0-9][A-Za-z0-9@/._\-]*)["']\s*:\s*["'^><=~]""")

# --- env keys, on ADDED lines in .env.example-style files ---------------------
_ENV_KEY_RE = re.compile(r"^\+\s*(?P<key>[A-Z][A-Z0-9_]+)\s*=")


@dataclass
class DiffSummary:
    files_changed: list[str] = field(default_factory=list)
    routes_added: list[dict] = field(default_factory=list)       # {route, method, file}
    dependencies_added: list[dict] = field(default_factory=list) # {name, version_spec, ecosystem, file}
    env_keys_added: list[dict] = field(default_factory=list)     # {key, file}

    @property
    def is_empty(self) -> bool:
        return not (self.routes_added or self.dependencies_added or self.env_keys_added)


def _is_env_example(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name == ".env.example" or name.startswith(".env.example") or name in {
        ".env.template",
        ".env.sample",
    }


def _extract_routes(line: str, path: str) -> list[dict]:
    found: list[dict] = []
    py_like = path.endswith((".py",))
    js_like = path.endswith((".ts", ".js", ".tsx", ".jsx", ".mjs", ".cjs"))

    for m in _API_ROUTE_RE.finditer(line):
        for raw in m.group("methods").split(","):
            method = raw.strip().strip("'\"")
            if method:
                found.append({"route": m.group("route"), "method": method.upper(), "file": path})
        return found

    if py_like:
        for m in _FASTAPI_RE.finditer(line):
            found.append({"route": m.group("route"), "method": m.group(1).upper(), "file": path})
        for m in _FLASK_RE.finditer(line):
            methods = (
                [x.strip().strip("'\"").upper() for x in m.group("methods").split(",") if x.strip()]
                if m.group("methods")
                else ["GET"]
            )
            found.extend({"route": m.group("route"), "method": x, "file": path} for x in methods)
    if js_like:
        for m in _EXPRESS_RE.finditer(line):
            method = "GET" if m.group(1) == "all" else m.group(1).upper()
            found.append({"route": m.group("route"), "method": method, "file": path})
    return found


def _extract_deps(line: str, path: str) -> list[dict]:
    name = path.rsplit("/", 1)[-1]
    if name == "requirements.txt":
        m = _REQUIREMENTS_RE.match(line)
        if m and not line.lstrip("+").lstrip().startswith(("#", "-")):
            return [
                {
                    "name": m.group("name"),
                    "version_spec": (m.group("spec") or "").strip(),
                    "ecosystem": "pypi",
                    "file": path,
                }
            ]
    if name == "pyproject.toml":
        m = _PYPROJECT_RE.match(line)
        if m:
            return [
                {
                    "name": m.group("name"),
                    "version_spec": (m.group("spec") or "").strip(),
                    "ecosystem": "pypi",
                    "file": path,
                }
            ]
    if name == "package.json":
        m = _PACKAGEJSON_RE.match(line)
        if m:
            return [
                {
                    "name": m.group("name"),
                    "version_spec": "",
                    "ecosystem": "npm",
                    "file": path,
                }
            ]
    return []


def parse_diff(diff_text: str) -> DiffSummary:
    """Parse unified-diff text into a DiffSummary. Pure function."""
    summary = DiffSummary()
    current_file: str | None = None

    for line in diff_text.splitlines():
        file_m = _DIFF_FILE_RE.match(line)
        if file_m:
            current_file = file_m.group("b")
            if current_file not in summary.files_changed:
                summary.files_changed.append(current_file)
            continue

        new_m = _NEW_PATH_RE.match(line)
        if new_m and new_m.group("path") != "/dev/null":
            current_file = new_m.group("path")
            if current_file not in summary.files_changed:
                summary.files_changed.append(current_file)
            continue

        # Only ADDED lines carry "new" facts (a removed route is not a new contract).
        if not line.startswith("+") or line.startswith("+++") or current_file is None:
            continue

        summary.routes_added.extend(_extract_routes(line, current_file))
        summary.dependencies_added.extend(_extract_deps(line, current_file))
        if _is_env_example(current_file):
            m = _ENV_KEY_RE.match(line)
            if m:
                summary.env_keys_added.append({"key": m.group("key"), "file": current_file})

    # Deduplicate identical findings
    summary.routes_added = [dict(t) for t in {tuple(sorted(r.items())) for r in summary.routes_added}]
    summary.dependencies_added = [
        dict(t) for t in {tuple(sorted(d.items())) for d in summary.dependencies_added}
    ]
    summary.env_keys_added = [dict(t) for t in {tuple(sorted(e.items())) for e in summary.env_keys_added}]
    return summary
