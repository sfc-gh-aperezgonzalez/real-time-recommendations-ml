"""Hard reset of the PlayNova Online Feature Store layer.

Cleans up after delete/recreate churn that can corrupt stream feature-view
metadata and orphan online writers. Drops all online feature tables, the online
service, and the FEATURES schema, so the feature store can be rebuilt from a
clean slate (rerun 03_dynamic_tables.sql + 04_feature_store_rbac.sql, then
`python ml/feature_store.py all`).
"""
from __future__ import annotations

from _session import DEMO_DB, get_session


def main() -> None:
    s = get_session()
    try:
        try:
            rows = s.sql(f"SHOW ONLINE FEATURE TABLES IN SCHEMA {DEMO_DB}.FEATURES").collect()
        except Exception as exc:  # noqa: BLE001
            rows = []
            print(f"show online feature tables: {exc}")
        for r in rows:
            d = r.as_dict()
            name = d.get("name") or d.get("NAME")
            try:
                s.sql(f'DROP ONLINE FEATURE TABLE IF EXISTS {DEMO_DB}.FEATURES."{name}"').collect()
                print(f"dropped online feature table {name}")
            except Exception as exc:  # noqa: BLE001
                print(f"drop online table {name}: {exc}")

        try:
            from feature_store import open_fs
            fs = open_fs(s)
            fs.drop_online_service()
            print("dropped online service")
        except Exception as exc:  # noqa: BLE001
            print(f"drop_online_service: {exc}")

        s.sql(f"DROP SCHEMA IF EXISTS {DEMO_DB}.FEATURES CASCADE").collect()
        print("dropped schema FEATURES (cascade)")
    finally:
        s.close()


if __name__ == "__main__":
    main()
