---
name: playnova-realtime-recommendations
description: Deploy the PlayNova fully-Snowflake-native real-time game recommendation demo — Online Feature Store (Postgres), Dynamic Tables, XGBoost ranker + propensity in Model Registry, an SPCS FastAPI orchestrator, a branded Next.js web app, and a Streamlit policy console. Phased (P0 preflight → P5 validation) with a net-zero teardown.
---

# PlayNova — Real-Time Game Recommendations (deployment skill)

A reusable, phased deployment of an end-to-end real-time recommendation system
for a fictitious iGaming operator, **PlayNova**. Everything runs in Snowflake and
the whole demo is **net-zero**: it creates its own database, dedicated managed
Postgres (via the OFS online service), compute pool, roles, and PAT, and the
`teardown` phase removes all of it.

## When to use
- Stand up the PlayNova demo in a fresh account.
- Showcase the Online Feature Store (Postgres) preview: batch + stream feature
  views, real-time serving, REST ingest/query.
- Demonstrate hybrid batch + real-time ML with deterministic policy control.

## Prerequisites (validated in P0)
- `snowflake-ml-python >= 1.41` in an isolated venv (OFS preview).
- Docker running **and signed in** (corporate Docker Desktop may enforce org login).
- `snow` CLI ≥ 3.x with a connection (default `default`), Node ≥ 18.
- Account with the **Online Feature Store (Postgres) preview** enabled and SPCS.
- Role with ACCOUNTADMIN-equivalent (CREATE DATABASE/ROLE/COMPUTE POOL/SERVICE).

## Phases (deterministic fast path: `deploy/deploy.sh`)
Run a phase or the whole pipeline (`CONN=<connection> deploy/deploy.sh <phase>`):

| Phase | Command | What it does |
|------|---------|--------------|
| **P0** preflight | `deploy/deploy.sh preflight` | venv + deps, Docker/Node/snow checks, **OFS readiness gate** (`ml/validate_ofs.py`). Hard-stops if OFS < 1.41 or not enabled. |
| **P1** bootstrap | `deploy/deploy.sh bootstrap` | DB `PLAYNOVA_RECS_DEMO`, schemas CORE/RAW/FEATURES/ML/APP, tables, tile stage, image repo, `PLAYNOVA_POOL`. |
| **P2** data | `deploy/deploy.sh data` | 12 regions, 12 categories, 240 games, 4000 segmented players, ~1M rounds with affinity skew, seeded policies, 240 branded tiles. |
| **P3** features | `deploy/deploy.sh features` | 5 Dynamic Tables, FS RBAC roles, **PAT + `APP.OFS_PAT` secret**, Feature Store + **online service (dedicated Postgres) + batch/stream/realtime FVs + feature group**, XGBoost ranker + propensity registered. |
| **P3b** inference | `deploy/deploy.sh inference` | Serve the ranker on SPCS (`ML.PLAYNOVA_RANKER_SVC`), grant endpoint usage, log real (backdated) predictions, and create the **ML Observability Model Monitor** (segmented by region). |
| **P4** apps | `deploy/deploy.sh apps` | Build/push orchestrator + Next.js images, deploy combined SPCS service `PLAYNOVA_APP` (web + orchestrator share localhost), deploy Streamlit `POLICY_CONSOLE`. |
| **P5** validate | `deploy/deploy.sh validate` | `pytest tests/` — ML/data integrity + spec §10 end-to-end smoke suite; prints the public web endpoint. |
| teardown | `deploy/deploy.sh teardown` | `ml/teardown.py --yes` — drops services, Streamlit, online service, pool, DB (cascade), FS roles, PAT. **Net-zero.** |

Full run: `CONN=default deploy/deploy.sh all`.

## Critical OFS lessons baked into this skill
- **Create the online service BEFORE registering online-enabled feature views**
  (registering an online FV with no service errors `No Online Service for this schema`).
- **Register stream feature views once, on a clean schema** — repeated
  delete/recreate corrupts stream metadata (`no writer available`,
  `Cannot find StreamSource`, `timestamp_col … not found`). Use `ml/reset_hard.py`
  for a clean rebuild.
- **Continuous-aggregation stream writers reliably support only `count`/`sum`** —
  `Feature.max(timestamp)` / `approx_count_distinct` can leave the writer
  unprovisioned. Stick to count/sum.
- `stream_ingest` must receive the **StreamSource object**, not its name.
- Online ingest/query require a **PAT** (`SNOWFLAKE_PAT`) + `httpx`.

## Architecture & data flow
See the root [`README.md`](../README.md) (architecture, data flow, and where the
model/ML code lives) and [`docs/architecture.png`](../docs/architecture.png).

## Key objects created
- DB `PLAYNOVA_RECS_DEMO` (CORE/RAW/FEATURES/ML/APP), `PLAYNOVA_POOL`,
  image repo `APP.PLAYNOVA_REPO`.
- Feature Store `FEATURES` + online service (dedicated Postgres); roles
  `PLAYNOVA_FS_PRODUCER/CONSUMER`; PAT `PLAYNOVA_OFS_PAT` + secret `APP.OFS_PAT`.
- Models `PLAYNOVA_RANKER`, `PLAYNOVA_PROPENSITY` (Model Registry).
- Ranker inference service `ML.PLAYNOVA_RANKER_SVC` (SPCS) + Model Monitor
  `ML.PLAYNOVA_RANKER_MONITOR` (ML Observability), backed by
  `ML.RANKER_INFERENCE_LOG` / `ML.RANKER_BASELINE`.
- Services: `APP.PLAYNOVA_APP` (web + orchestrator), Streamlit `APP.POLICY_CONSOLE`.

## Troubleshooting
| Symptom | Fix |
|--------|-----|
| `No Online Service for this schema` | Create the online service first (P3 order is correct). |
| `no writer available for FV` | Clean rebuild via `ml/reset_hard.py` then P3; ensure count/sum-only stream features. |
| Docker build: org sign-in required | Sign in to Docker Desktop (corporate enforced), re-run P4. |
| Service spec CPU error | Container CPU requests must fit the instance family (XS = 1 vCPU). |
| Policy change not reflected | The orchestrator enforces market policy **live**; the `MARKET_ELIGIBLE_GAMES` DT (1h lag) is only for the Streamlit preview. |
