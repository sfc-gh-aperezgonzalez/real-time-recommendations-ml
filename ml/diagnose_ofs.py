"""Diagnose the Online Feature Store serving path after a clean rebuild.

Checks (independently so one failure doesn't mask others):
  1. online service status
  2. batch online read (PLAYER_BEHAVIOR_FV) - no writer needed
  3. stream FV reconstruction (get_feature_view) + suspend/resume to kick writer
  4. one stream ingest attempt after resume
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from feature_store import build_stream_source, open_fs, V  # noqa: E402
from _session import get_session  # noqa: E402


def main() -> None:
    s = get_session()
    fs = open_fs(s)

    print("== 1. online service status ==")
    try:
        st = fs.get_online_service_status()
        print("status:", st.status, "endpoints:", [e.name for e in (st.endpoints or [])])
    except Exception as e:  # noqa: BLE001
        print("status err:", e)

    print("== 2. batch online read PLAYER_BEHAVIOR_FV ==")
    try:
        fv = fs.get_feature_view("PLAYER_BEHAVIOR_FV", V)
        df = fs.read_feature_view(fv, keys=[["1"]], feature_names=["ROUNDS_30D", "TOTAL_GGR_EUR"], store_type="online")
        df.show()
    except Exception as e:  # noqa: BLE001
        print("batch online read err:", repr(e)[:300])

    print("== 3. stream FV get + suspend/resume ==")
    for n in ("USER_RECENT_ACTIVITY",):
        try:
            fv = fs.get_feature_view(n, V)
            print(f"get_feature_view {n}: OK status={fv.status}")
        except Exception as e:  # noqa: BLE001
            print(f"get_feature_view {n} err:", repr(e)[:200])
        for op in ("suspend", "resume"):
            try:
                fn = getattr(fs, f"{op}_feature_view")
                try:
                    fn(n, V)
                except TypeError:
                    fn(fs.get_feature_view(n, V))
                print(f"{op}_feature_view {n}: OK")
            except Exception as e:  # noqa: BLE001
                print(f"{op}_feature_view {n} err:", repr(e)[:200])

    print("== 4. ingest attempt after resume ==")
    import datetime as dt
    ev = [{"PLAYER_ID": 1, "EVENT_TS": dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
           "EVENT_TYPE": "PLAY", "GAME_TITLE_ID": 1000, "CATEGORY_ID": 1, "REGION_CODE": "UK",
           "STAKE_AMT": 1.5, "SESSION_ID": "diag-1", "GAME_KEY": "1000"}]
    for attempt in range(6):
        try:
            n = fs.stream_ingest(build_stream_source(), ev)
            print(f"ingest OK: {n} (attempt {attempt+1})")
            break
        except Exception as e:  # noqa: BLE001
            print(f"  ingest attempt {attempt+1}:", repr(e)[:160])
            time.sleep(20)
    s.close()


if __name__ == "__main__":
    main()
