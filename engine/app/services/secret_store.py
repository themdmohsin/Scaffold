"""secret_store.py — the ONLY module in the codebase that touches secret VALUES.

Repo rule honored: "never store plaintext secrets in ordinary PostgreSQL fields."
Values live ONLY in `scaffold_secrets.environment_secrets.encrypted`, wrapped
with pgcrypto's PGP symmetric encryption (AES-256) under a keyring row that
lives in the same database. Three hard properties:

1. ONE WRITER/READER. Every other module (routes, MCP tools, retrieval, the
   reason pipeline) reaches secret metadata through the ORM — but VALUES only
   through the two functions below. A code search for "environment_secrets"
   finds this file and the migration, nothing else.

2. THE PLAINTEXT NEVER PERSISTS OUTSIDE THE CIPHERTEXT COLUMN. Writes bind the
   value as a single-statement CTE: `pgp_sym_encrypt(:value, key)` runs
   server-side; the plaintext exists in the statement's bind parameters and in
   the ciphertext column, nowhere else (no temp table, no intermediate column).
   Reads decrypt server-side in the SELECT and return the value to the
   (authorized) caller's memory only.

3. THE WRAPPING KEY NEVER LEAVES THE DATABASE. The key material sits in
   `scaffold_secrets.keyring.key_id` and is resolved by a subquery INSIDE the
   same statement — the engine never selects the key into Python. If the
   optional SCAFFOLD_SECRET_KEYRING setting is set, that keyring name is
   pinned; otherwise the newest row (created_at desc) is used.

HONEST LIMITATION (documented per the Phase 5 brief): pgcrypto symmetric
encryption with a database-resident key protects against value leakage through
Scaffold's own surface (API responses, logs, events, embeddings, prompts, dash-
board, SQL dumps of `public`) — anyone who can query `scaffold_secrets.keyring`
AND `environment_secrets` as the service role can decrypt. That is the same
trust tier as Supabase Vault's default key, but Vault's column-encryption
internals need superuser privileges this project's service role does not have
(verified against the live database — see docs/SCHEMA.md Phase 5). Upgrading to
an external KMS / Secret Manager later means swapping exactly the two functions
below; no route or service changes.

No LLM anywhere in this file (repo rule #3).
"""

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

_SCHEMA = "scaffold_secrets"


class SecretStoreUnavailable(RuntimeError):
    """Raised when the pgcrypto extension or the keyring is missing/broken.

    Fail-closed BY DESIGN: a broken secret store must never silently fall back
    to storing a value in plaintext.
    """


def _keyring_clause() -> str:
    """SQL fragment that resolves the wrapping key inside the statement.

    Pinned name (SCAFFOLD_SECRET_KEYRING) or the newest keyring row.
    """
    pinned = (settings.scaffold_secret_keyring or "").strip()
    if pinned:
        return (
            "(SELECT key_id FROM scaffold_secrets.keyring "
            "WHERE name = :keyring_name ORDER BY created_at DESC LIMIT 1)"
        )
    return (
        "(SELECT key_id FROM scaffold_secrets.keyring "
        "ORDER BY created_at DESC LIMIT 1)"
    )


def _keyring_params() -> dict:
    pinned = (settings.scaffold_secret_keyring or "").strip()
    return {"keyring_name": pinned} if pinned else {}


def set_value(db: Session, environment_variable_id: uuid.UUID, value: str) -> None:
    """Encrypt + upsert one secret value. Single statement: the plaintext is
    only ever a bind parameter and the ciphertext column, never a table/column
    of its own, never returned to the client."""
    stmt = text(
        f"""
        INSERT INTO {_SCHEMA}.environment_secrets
            (environment_variable_id, encrypted, key_id, updated_at)
        SELECT :env_var_id,
               pgp_sym_encrypt(:value, k.key_id, 'cipher-algo=aes256'),
               k.id,
               now()
        FROM scaffold_secrets.keyring k
        WHERE k.key_id = {_keyring_clause()}
        ON CONFLICT (environment_variable_id) DO UPDATE
        SET encrypted  = EXCLUDED.encrypted,
            key_id     = EXCLUDED.key_id,
            updated_at = now()
        """
    )
    params = {"env_var_id": environment_variable_id, "value": value, **_keyring_params()}
    result = db.execute(stmt, params)
    if result.rowcount != 1:
        db.rollback()
        raise SecretStoreUnavailable(
            "secret store unavailable: no usable scaffold_secrets.keyring row "
            "(pgcrypto keyring missing or SCAFFOLD_SECRET_KEYRING names no row)"
        )


def clear_value(db: Session, environment_variable_id: uuid.UUID) -> None:
    """Remove the stored value (DELETE /variables uses this to honor rule:
    no orphan ciphertext after metadata deletion)."""
    db.execute(
        text(f"DELETE FROM {_SCHEMA}.environment_secrets WHERE environment_variable_id = :id"),
        {"id": environment_variable_id},
    )


def get_value(db: Session, environment_variable_id: uuid.UUID) -> str | None:
    """Decrypt one secret value server-side; None when no value is stored.

    Caller contract (enforced by routes/environment.py): only reached after the
    membership + grant check, and the result goes straight into the response of
    the retrieval endpoints — never into events, logs, or any other payload.
    """
    stmt = text(
        f"""
        SELECT pgp_sym_decrypt(s.encrypted, k.key_id) AS value
        FROM {_SCHEMA}.environment_secrets s
        JOIN scaffold_secrets.keyring k ON k.id = s.key_id
        WHERE s.environment_variable_id = :env_var_id
        """
    )
    row = db.execute(stmt, {"env_var_id": environment_variable_id}).first()
    return row.value if row else None


def store_ok(db: Session) -> bool:
    """True when the ciphertext store is usable (pgcrypto + keyring present).
    Used by tests and by the endpoints' 503 diagnostics — never by reads."""
    try:
        db.execute(text("SELECT 1 FROM scaffold_secrets.keyring LIMIT 1")).first()
        return True
    except Exception:
        db.rollback()
        return False
