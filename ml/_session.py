"""Shared Snowpark session helper for the PlayNova demo.

Always connects to the SE demo account via the ``default`` connection in
``~/.snowflake/connections.toml`` (account SFSEEUROPE-APEREZ_AWS1). The toml
``default_connection_name`` is ``snowhouse`` (a different, internal account), so
we must pass the connection name explicitly.
"""
from __future__ import annotations

import os

from snowflake.snowpark import Session

CONNECTION_NAME = os.environ.get("PLAYNOVA_CONNECTION", "default")

DEMO_DB = os.environ.get("PLAYNOVA_DB", "PLAYNOVA_RECS_DEMO")
DEMO_WH = os.environ.get("PLAYNOVA_WH", "COMPUTE_WH")


def get_session() -> Session:
    """Create a Snowpark session bound to the PlayNova demo account."""
    session = Session.builder.config("connection_name", CONNECTION_NAME).create()
    return session


if __name__ == "__main__":
    s = get_session()
    row = s.sql(
        "SELECT CURRENT_ACCOUNT() AS A, CURRENT_ROLE() AS R, CURRENT_REGION() AS REG"
    ).collect()[0]
    print(f"account={row['A']} role={row['R']} region={row['REG']}")
    s.close()
