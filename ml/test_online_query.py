"""Probe the OFS Query REST API directly to confirm request/response shape, so the
orchestrator's ofs_query() parses correctly. Prints raw JSON for a player.

Run after the online service is RUNNING and at least one event ingested:
    SNOWFLAKE_PAT="$(cat .pat_token)" .venv/bin/python ml/test_online_query.py
"""
from __future__ import annotations

import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(__file__))
from feature_store import open_fs, online_service  # noqa: E402
from _session import get_session  # noqa: E402

PAT = os.environ["SNOWFLAKE_PAT"]


def query(url: str, name: str, version: str, entity: dict, features=None, object_type="feature_view"):
    body = {
        "name": name, "version": version, "object_type": object_type,
        "metadata_options": {"include_names": True, "include_data_types": True},
        "request_rows": [{"entity": entity}],
    }
    if features:
        body["features"] = features
    r = requests.post(
        f"{url}/api/v1/query",
        headers={"Authorization": f'Snowflake Token="{PAT}"', "Content-Type": "application/json"},
        json=body, timeout=8,
    )
    print(f"--- {name} status={r.status_code} ---")
    print(json.dumps(r.json(), indent=2, default=str)[:1500])


def main() -> None:
    s = get_session()
    fs = open_fs(s)
    st = fs.get_online_service_status()
    qurl = online_service.endpoint_url(st, "query")
    print("query url:", qurl)
    query(qurl, "USER_RECENT_ACTIVITY", "V1", {"PLAYER_ID": 1})
    query(qurl, "PLAYER_BEHAVIOR_FV", "V1", {"PLAYER_ID": 1})
    query(qurl, "USER_CATEGORY_RECENT", "V1", {"PLAYER_ID": 1})
    s.close()


if __name__ == "__main__":
    main()
