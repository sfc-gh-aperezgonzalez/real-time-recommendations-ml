#!/usr/bin/env bash
# =============================================================================
# PlayNova Real-Time Recommendations - phased deployment (P0-P5) + teardown
# Net-zero: every object is created under PLAYNOVA_RECS_DEMO + the dedicated
# PLAYNOVA_POOL + FS roles + PAT, and `teardown` removes all of it.
#
# Usage:
#   deploy/deploy.sh preflight|bootstrap|data|features|apps|validate|all|teardown
#   CONN=default deploy/deploy.sh all
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
CONN="${CONN:-default}"
PY="$ROOT/.venv/bin/python"
PIP="$ROOT/.venv/bin/pip"
SNOW="snow sql --connection $CONN"
export PLAYNOVA_CONNECTION="$CONN"

log() { printf "\n\033[1;35m[playnova] %s\033[0m\n" "$*"; }

repo_url() {
  snow sql --connection "$CONN" -q "SHOW IMAGE REPOSITORIES IN SCHEMA PLAYNOVA_RECS_DEMO.APP" --format json 2>/dev/null \
    | "$PY" -c "import sys,json;print(json.load(sys.stdin)[0]['repository_url'])"
}

p0_preflight() {
  log "P0 preflight: venv, OFS readiness (>=1.41), tooling"
  [ -d .venv ] || python3 -m venv .venv
  $PIP install --quiet -r requirements-dev.txt
  command -v docker >/dev/null && docker info >/dev/null 2>&1 || { echo "Docker not running"; exit 1; }
  command -v node >/dev/null || { echo "node missing"; exit 1; }
  command -v snow >/dev/null || { echo "snow CLI missing"; exit 1; }
  $PY ml/validate_ofs.py
}

p1_bootstrap() {
  log "P1 bootstrap: database, schemas, tables, image repo, compute pool"
  $SNOW -f sql/01_ddl.sql >/dev/null
  $SNOW -q "CREATE IMAGE REPOSITORY IF NOT EXISTS PLAYNOVA_RECS_DEMO.APP.PLAYNOVA_REPO" >/dev/null
  $SNOW -q "CREATE COMPUTE POOL IF NOT EXISTS PLAYNOVA_POOL MIN_NODES=1 MAX_NODES=2 INSTANCE_FAMILY=CPU_X64_XS AUTO_RESUME=TRUE AUTO_SUSPEND_SECS=3600" >/dev/null
}

p2_data() {
  log "P2 data: 240 games, 4000 players, ~1M rounds, policies, tiles"
  $SNOW -f sql/02_mock_data.sql >/dev/null
  $PY ml/generate_tiles.py
}

p3_features() {
  log "P3 features+models: Dynamic Tables, RBAC, OFS (online service + FVs), models"
  $SNOW -f sql/03_dynamic_tables.sql >/dev/null
  $SNOW -f sql/04_feature_store_rbac.sql >/dev/null
  $PY ml/create_pat_secret.py
  SNOWFLAKE_PAT="$(cat .pat_token)" $PY ml/feature_store.py all
  $PY ml/train.py
}

p3b_inference() {
  log "P3b inference+observability: serve ranker on SPCS + ML Observability monitor"
  $SNOW -f sql/06_ml_observability.sql >/dev/null       # inference-log + baseline tables
  $PY ml/deploy_inference.py                            # PLAYNOVA_RANKER_SVC (SPCS, ~10 min first build)
  $SNOW -q "GRANT SERVICE ROLE PLAYNOVA_RECS_DEMO.ML.PLAYNOVA_RANKER_SVC!ALL_ENDPOINTS_USAGE TO ROLE ACCOUNTADMIN" >/dev/null
  # Real predictions from the deployed service (backdated timestamps) + create the monitor.
  $PY ml/backfill_inference.py --days 7 --players-per-region-per-day 2 --fresh --create-monitor
}

p4_apps() {
  log "P4 apps: build/push images, deploy combined SPCS service + Streamlit"
  snow spcs image-registry login --connection "$CONN" >/dev/null
  local REPO; REPO="$(repo_url)"
  # Sync game tiles into the app's public dir before the image build.
  # AI-generated JPGs (Method C) are the primary art; SVGs remain as fallback.
  cp -f assets/tiles_ai/*.jpg app/public/tiles/ 2>/dev/null || true
  cp -f assets/tiles/*.svg app/public/tiles/ 2>/dev/null || true
  ( cd services/orchestrator && docker build --platform linux/amd64 -t orchestrator:latest . \
      && docker tag orchestrator:latest "$REPO/orchestrator:latest" && docker push "$REPO/orchestrator:latest" )
  ( cd app && docker build --platform linux/amd64 -t playnova-app:latest . \
      && docker tag playnova-app:latest "$REPO/playnova-app:latest" && docker push "$REPO/playnova-app:latest" )
  $SNOW -f deploy/service.sql >/dev/null
  $SNOW -f deploy/streamlit.sql >/dev/null
  log "Web endpoint:"; $SNOW -q "SHOW ENDPOINTS IN SERVICE PLAYNOVA_RECS_DEMO.APP.PLAYNOVA_APP" || true
}

p5_validate() {
  log "P5 validate: ML + end-to-end smoke suite (spec section 10)"
  SNOWFLAKE_PAT="$(cat .pat_token)" $PY -m pytest tests/ -q
  log "Endpoints:"; $SNOW -q "SHOW ENDPOINTS IN SERVICE PLAYNOVA_RECS_DEMO.APP.PLAYNOVA_APP"
}

teardown() { log "Teardown (net-zero)"; $PY ml/teardown.py --yes; }

case "${1:-all}" in
  preflight) p0_preflight ;;
  bootstrap) p1_bootstrap ;;
  data)      p2_data ;;
  features)  p3_features ;;
  inference) p3b_inference ;;
  apps)      p4_apps ;;
  validate)  p5_validate ;;
  teardown)  teardown ;;
  all)       p0_preflight; p1_bootstrap; p2_data; p3_features; p3b_inference; p4_apps; p5_validate;
             log "DONE. PlayNova deployed." ;;
  *) echo "usage: $0 preflight|bootstrap|data|features|inference|apps|validate|all|teardown"; exit 1 ;;
esac
