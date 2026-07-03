"""Net-zero teardown of the entire PlayNova demo.

Removes every object the demo created so the account is left clean:
  SPCS services, Streamlit, OFS online service (+ managed Postgres), compute pool,
  the database (cascade: schemas, Feature Store, Dynamic Tables, image repo,
  stages, secret), the FS roles, and the PAT.

Usage: python ml/teardown.py [--yes]
"""
from __future__ import annotations

import sys

from _session import DEMO_DB, get_session

DROP_POOL = "PLAYNOVA_POOL"
FS_ROLES = ["PLAYNOVA_FS_PRODUCER", "PLAYNOVA_FS_CONSUMER"]
PAT_NAME = "PLAYNOVA_OFS_PAT"


def _run(s, sql, label):
    try:
        s.sql(sql).collect()
        print(f"[teardown] OK: {label}")
    except Exception as exc:  # noqa: BLE001
        print(f"[teardown] skip {label}: {str(exc)[:120]}")


def main() -> None:
    if "--yes" not in sys.argv:
        print("This DROPS the entire PlayNova demo. Re-run with --yes to confirm.")
        return
    s = get_session()
    try:
        # 1. SPCS services + Streamlit (free the compute pool).
        _run(s, f"DROP MODEL MONITOR IF EXISTS {DEMO_DB}.ML.PLAYNOVA_RANKER_MONITOR", "monitor PLAYNOVA_RANKER_MONITOR")
        _run(s, f"DROP SERVICE IF EXISTS {DEMO_DB}.ML.PLAYNOVA_RANKER_SVC", "service PLAYNOVA_RANKER_SVC")
        _run(s, f"DROP SERVICE IF EXISTS {DEMO_DB}.APP.PLAYNOVA_APP", "service PLAYNOVA_APP")
        _run(s, f"DROP SERVICE IF EXISTS {DEMO_DB}.APP.PLAYNOVA_ORCH", "service PLAYNOVA_ORCH")
        _run(s, f"DROP STREAMLIT IF EXISTS {DEMO_DB}.APP.POLICY_CONSOLE", "streamlit POLICY_CONSOLE")

        # 2. OFS online service (managed Postgres) - drop online feature tables first.
        try:
            rows = s.sql(f"SHOW ONLINE FEATURE TABLES IN SCHEMA {DEMO_DB}.FEATURES").collect()
            for r in rows:
                d = r.as_dict(); name = d.get("name") or d.get("NAME")
                _run(s, f'DROP ONLINE FEATURE TABLE IF EXISTS {DEMO_DB}.FEATURES."{name}"', f"online table {name}")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] list online tables: {str(exc)[:100]}")
        try:
            from feature_store import open_fs
            open_fs(s).drop_online_service()
            print("[teardown] OK: online service dropped")
        except Exception as exc:  # noqa: BLE001
            print(f"[teardown] skip online service: {str(exc)[:120]}")

        # 3. Database (cascade removes schemas, FS, DTs, image repo, stages, secret).
        _run(s, f"DROP DATABASE IF EXISTS {DEMO_DB} CASCADE", f"database {DEMO_DB}")

        # 4. Compute pool.
        _run(s, f"DROP COMPUTE POOL IF EXISTS {DROP_POOL}", f"compute pool {DROP_POOL}")

        # 5. Feature Store roles.
        for role in FS_ROLES:
            _run(s, f"DROP ROLE IF EXISTS {role}", f"role {role}")

        # 6. PAT.
        user = s.sql("SELECT CURRENT_USER() U").collect()[0]["U"]
        _run(s, f"ALTER USER {user} REMOVE PROGRAMMATIC ACCESS TOKEN {PAT_NAME}", f"PAT {PAT_NAME}")
        print("[teardown] DONE - account is net-zero for PlayNova.")
    finally:
        s.close()


if __name__ == "__main__":
    main()
