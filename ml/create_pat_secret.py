"""Create a Programmatic Access Token (PAT) for Online Feature Store auth and
store it as a Snowflake secret for the SPCS orchestrator.

The OFS ingest + query REST APIs require a PAT (`Authorization: Snowflake
Token="<pat>"`). This script:
  1. (re)creates a PAT on the current user,
  2. creates/replaces APP.OFS_PAT (GENERIC_STRING secret) for the orchestrator,
  3. writes the token to a local gitignored file for local testing.

The token value is never printed to stdout.
"""
from __future__ import annotations

import os
import stat

from _session import DEMO_DB, get_session

PAT_NAME = "PLAYNOVA_OFS_PAT"
LOCAL_TOKEN_FILE = os.path.join(os.path.dirname(__file__), "..", ".pat_token")


def main() -> None:
    s = get_session()
    try:
        user = s.sql("SELECT CURRENT_USER() U").collect()[0]["U"]
        # Remove any prior token of this name, then add a fresh one.
        try:
            s.sql(f"ALTER USER {user} REMOVE PROGRAMMATIC ACCESS TOKEN {PAT_NAME}").collect()
        except Exception:  # noqa: BLE001
            pass
        rows = s.sql(
            f"ALTER USER {user} ADD PROGRAMMATIC ACCESS TOKEN {PAT_NAME} "
            f"ROLE_RESTRICTION = 'ACCOUNTADMIN' DAYS_TO_EXPIRY = 30"
        ).collect()
        token = None
        for r in rows:
            d = r.as_dict()
            for k, v in d.items():
                if "secret" in k.lower() or k.lower() == "token_secret":
                    token = v
        if not token:
            raise RuntimeError(f"could not read token from ALTER USER result: {list(rows[0].as_dict().keys())}")

        # Store as a Snowflake secret for the orchestrator (token stays in Snowflake).
        s.sql(f"CREATE SCHEMA IF NOT EXISTS {DEMO_DB}.APP").collect()
        s.sql(
            f"CREATE OR REPLACE SECRET {DEMO_DB}.APP.OFS_PAT "
            f"TYPE = GENERIC_STRING SECRET_STRING = '{token}'"
        ).collect()

        # Local file for testing (gitignored).
        path = os.path.abspath(LOCAL_TOKEN_FILE)
        with open(path, "w") as f:
            f.write(token)
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        print(f"OK: PAT '{PAT_NAME}' created, secret {DEMO_DB}.APP.OFS_PAT set, token written to {path}")
    finally:
        s.close()


if __name__ == "__main__":
    main()
