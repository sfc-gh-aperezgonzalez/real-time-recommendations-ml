"""Snowflake connectivity for the orchestrator.

Detects SPCS (OAuth token at /snowflake/session/token) vs local (connections.toml
'default'). Exposes a cached Snowpark session and a small run_query() helper that
returns a list of dicts.
"""
from __future__ import annotations

import os
from typing import Any

from snowflake.snowpark import Session
from snowflake.snowpark.exceptions import SnowparkSQLException

SPCS_TOKEN_PATH = "/snowflake/session/token"

_session: Session | None = None


def _build_session() -> Session:
    if os.path.exists(SPCS_TOKEN_PATH):
        # SPCS rotates this token file; always read the current token when
        # (re)building the session so we never hold a stale/expired one.
        with open(SPCS_TOKEN_PATH) as f:
            token = f.read()
        cfg = {
            "account": os.environ["SNOWFLAKE_ACCOUNT"],
            "host": os.environ["SNOWFLAKE_HOST"],
            "authenticator": "oauth",
            "token": token,
            "warehouse": os.environ.get("PLAYNOVA_WH", "COMPUTE_WH"),
            "database": os.environ.get("PLAYNOVA_DB", "PLAYNOVA_RECS_DEMO"),
        }
        return Session.builder.configs(cfg).create()
    # Local development
    return Session.builder.config("connection_name", os.environ.get("PLAYNOVA_CONNECTION", "default")).create()


def get_session() -> Session:
    global _session
    if _session is None:
        _session = _build_session()
    return _session


def _reset_session() -> None:
    global _session
    try:
        if _session is not None:
            _session.close()
    except Exception:
        pass
    _session = None


def run_query(sql: str) -> list[dict[str, Any]]:
    try:
        rows = get_session().sql(sql).collect()
    except SnowparkSQLException as e:
        # SPCS OAuth token expiry (390114). Rebuild the session from the
        # freshly rotated token file and retry once.
        if "390114" in str(e) or "Authentication token has expired" in str(e):
            _reset_session()
            rows = get_session().sql(sql).collect()
        else:
            raise
    return [row.as_dict() for row in rows]
