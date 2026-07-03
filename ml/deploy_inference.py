"""Deploy PLAYNOVA_RANKER as a Snowflake Model Registry real-time inference service.

Serves the registered ranker (PLAYNOVA_RECS_DEMO.ML.PLAYNOVA_RANKER / V1) as an
autoscaling HTTP endpoint on Snowpark Container Services (SPCS), so inference runs
inside Snowflake (observable in Model Registry -> Inference Services and via ML
Observability). The orchestrator calls the /predict endpoint at request time.

Idempotent: if the service already exists it is reused (endpoints are printed).

CLI:
    python ml/deploy_inference.py            # create (or reuse) + print endpoints
    python ml/deploy_inference.py --status   # just print endpoints
"""
from __future__ import annotations

import argparse

from snowflake.ml.registry import Registry

from _session import DEMO_DB, get_session

MODEL_NAME = "PLAYNOVA_RANKER"
VERSION = "V2"  # V2 adds the real-time RECENT_CAT_ACTIVITY_NORM feature
SERVICE_NAME = "PLAYNOVA_RANKER_SVC"
COMPUTE_POOL = "PLAYNOVA_POOL"
# Keep the service warm for demo sessions. The orchestrator calls the service over
# the INTERNAL service-mesh DNS (internet egress is disabled), and internal calls to
# a suspended service do NOT auto-resume it (only public ingress does) -- so a short
# idle timeout would silently drop inference to the heuristic fallback. A long window
# avoids that; teardown still removes the service (net-zero). Set 0 to disable suspend
# if your account allows it. Lower this to save idle credits when not demoing.
AUTO_SUSPEND_SECS = 86400  # 24h


def set_auto_suspend(session) -> None:
    fqsvc = f"{DEMO_DB}.ML.{SERVICE_NAME}"
    try:
        session.sql(f"ALTER SERVICE {fqsvc} SET AUTO_SUSPEND_SECS = {AUTO_SUSPEND_SECS}").collect()
        print(f"[deploy_inference] AUTO_SUSPEND_SECS set to {AUTO_SUSPEND_SECS}")
    except Exception as exc:  # noqa: BLE001
        print(f"[deploy_inference] could not set auto-suspend: {exc}")


def get_mv(session):
    reg = Registry(session, database_name=DEMO_DB, schema_name="ML")
    return reg.get_model(MODEL_NAME).version(VERSION)


def print_endpoints(mv) -> None:
    try:
        svcs = mv.list_services()
        print("[deploy_inference] services for PLAYNOVA_RANKER/V1:")
        print(svcs.to_string() if hasattr(svcs, "to_string") else svcs)
    except Exception as exc:  # noqa: BLE001
        print(f"[deploy_inference] list_services: {exc}")


def already_deployed(mv) -> bool:
    try:
        svcs = mv.list_services()
        names = []
        # list_services returns a pandas DataFrame; be defensive about columns.
        for col in ("name", "service_name", "SERVICE_NAME", "NAME"):
            if hasattr(svcs, "columns") and col in svcs.columns:
                names = [str(v).upper() for v in svcs[col].tolist()]
                break
        return any(SERVICE_NAME.upper() in n for n in names)
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="only print current service endpoints")
    args = ap.parse_args()

    session = get_session()
    try:
        mv = get_mv(session)
        if args.status:
            print_endpoints(mv)
            return

        if already_deployed(mv):
            print(f"[deploy_inference] service {SERVICE_NAME} already exists - reusing")
            set_auto_suspend(session)
            print_endpoints(mv)
            return

        print(f"[deploy_inference] creating service {SERVICE_NAME} on pool {COMPUTE_POOL} "
              f"(first CPU build can take ~10 min)...")
        mv.create_service(
            service_name=SERVICE_NAME,
            service_compute_pool=COMPUTE_POOL,
            image_build_compute_pool=COMPUTE_POOL,
            ingress_enabled=True,
            max_instances=1,
            gpu_requests=None,
        )
        print(f"[deploy_inference] service {SERVICE_NAME} created")
        set_auto_suspend(session)
        print_endpoints(mv)
    finally:
        session.close()


if __name__ == "__main__":
    main()
