"""Task 0 GATE: validate Online Feature Store readiness on the target account.

Checks, in order:
  1. snowflake-ml-python >= 1.41 is installed and OFS preview symbols import.
  2. A Snowpark session can connect to the target account.
  3. The OFS online-service system function is available (preview enabled).
  4. A FeatureStore can be created in a scratch schema (cleaned up after).

Exits non-zero with a clear message if any check fails, so the deployment
skill can fail fast before provisioning anything.
"""
from __future__ import annotations

import sys
from importlib import metadata

MIN_VERSION = (1, 41)


def _fail(msg: str) -> None:
    print(f"[OFS-GATE][FAIL] {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"[OFS-GATE][OK]   {msg}")


def check_client_version() -> None:
    ver = metadata.version("snowflake-ml-python")
    parts = tuple(int(x) for x in ver.split(".")[:2])
    if parts < MIN_VERSION:
        _fail(f"snowflake-ml-python {ver} < required 1.41")
    _ok(f"snowflake-ml-python {ver} (>= 1.41)")


def check_imports() -> None:
    try:
        from snowflake.ml.feature_store import (  # noqa: F401
            CreationMode,
            Entity,
            Feature,
            FeatureGroup,
            FeatureStore,
            FeatureView,
            OnlineConfig,
            OnlineStoreType,
            RealtimeConfig,
            RequestSource,
            StreamConfig,
            StreamSource,
        )
    except Exception as exc:  # noqa: BLE001
        _fail(f"OFS preview symbols failed to import: {exc}")
    _ok("OFS preview symbols import (batch/stream/realtime/feature-group)")


def main() -> None:
    check_client_version()
    check_imports()

    from _session import DEMO_WH, get_session
    from snowflake.ml.feature_store import CreationMode, FeatureStore

    session = get_session()
    try:
        acct = session.sql(
            "SELECT CURRENT_ACCOUNT() A, CURRENT_REGION() R"
        ).collect()[0]
        _ok(f"connected: account={acct['A']} region={acct['R']}")

        # OFS online-service system function must resolve (preview enabled).
        try:
            session.sql(
                "SELECT SYSTEM$GET_FEATURE_STORE_ONLINE_SERVICE_STATUS('PLAYNOVA_RECS_DEMO.OFS_GATE_SCRATCH')"
            ).collect()
            _ok("OFS online-service system function is available")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            # 'does not exist' for the FS arg is fine; 'unknown function' is not.
            if "unknown function" in msg or "invalid identifier" in msg and "system$" in msg:
                _fail(f"OFS online-service system function not available: {exc}")
            _ok("OFS online-service system function recognized (FS not yet created)")

        # FeatureStore creation in a scratch schema proves ML feature-store access.
        session.sql("CREATE DATABASE IF NOT EXISTS PLAYNOVA_GATE_SCRATCH").collect()
        fs = FeatureStore(
            session=session,
            database="PLAYNOVA_GATE_SCRATCH",
            name="FS_GATE",
            default_warehouse=DEMO_WH,
            creation_mode=CreationMode.CREATE_IF_NOT_EXIST,
        )
        _ok(f"FeatureStore created/opened: {fs._config.full_schema_path if hasattr(fs, '_config') else 'FS_GATE'}")
        session.sql("DROP DATABASE IF EXISTS PLAYNOVA_GATE_SCRATCH").collect()
        _ok("scratch cleaned up")
        print("[OFS-GATE][PASS] Online Feature Store is ready on this account.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
