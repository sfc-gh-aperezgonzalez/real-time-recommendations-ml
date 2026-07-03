-- Idempotent deploy of the combined PlayNova service (web app + orchestrator).
-- First run creates it; subsequent runs ALTER to pick up refreshed images.
CREATE SERVICE IF NOT EXISTS PLAYNOVA_RECS_DEMO.APP.PLAYNOVA_APP
  IN COMPUTE POOL PLAYNOVA_POOL
  FROM SPECIFICATION $$
spec:
  containers:
    - name: orchestrator
      image: /PLAYNOVA_RECS_DEMO/APP/PLAYNOVA_REPO/orchestrator:latest
      env: {PLAYNOVA_DB: PLAYNOVA_RECS_DEMO, PLAYNOVA_WH: COMPUTE_WH, MODEL_VERSION: ranker-v1, PORT: "8080"}
      secrets:
        - snowflakeSecret: PLAYNOVA_RECS_DEMO.APP.OFS_PAT
          envVarName: SNOWFLAKE_PAT
          secretKeyRef: secret_string
      resources: {requests: {memory: 1.5Gi, cpu: "0.5"}, limits: {memory: 3Gi, cpu: "1.5"}}
      readinessProbe: {port: 8080, path: /health}
    - name: webapp
      image: /PLAYNOVA_RECS_DEMO/APP/PLAYNOVA_REPO/playnova-app:latest
      env: {PORT: "3000", HOSTNAME: "0.0.0.0", NODE_ENV: production, ORCHESTRATOR_URL: "http://localhost:8080"}
      resources: {requests: {memory: 512Mi, cpu: "0.3"}, limits: {memory: 1Gi, cpu: "1"}}
      readinessProbe: {port: 3000, path: /login}
  endpoints:
    - name: web
      port: 3000
      public: true
$$
  MIN_INSTANCES = 1 MAX_INSTANCES = 1 QUERY_WAREHOUSE = COMPUTE_WH;

ALTER SERVICE PLAYNOVA_RECS_DEMO.APP.PLAYNOVA_APP FROM SPECIFICATION $$
spec:
  containers:
    - name: orchestrator
      image: /PLAYNOVA_RECS_DEMO/APP/PLAYNOVA_REPO/orchestrator:latest
      env: {PLAYNOVA_DB: PLAYNOVA_RECS_DEMO, PLAYNOVA_WH: COMPUTE_WH, MODEL_VERSION: ranker-v1, PORT: "8080"}
      secrets:
        - snowflakeSecret: PLAYNOVA_RECS_DEMO.APP.OFS_PAT
          envVarName: SNOWFLAKE_PAT
          secretKeyRef: secret_string
      resources: {requests: {memory: 1.5Gi, cpu: "0.5"}, limits: {memory: 3Gi, cpu: "1.5"}}
      readinessProbe: {port: 8080, path: /health}
    - name: webapp
      image: /PLAYNOVA_RECS_DEMO/APP/PLAYNOVA_REPO/playnova-app:latest
      env: {PORT: "3000", HOSTNAME: "0.0.0.0", NODE_ENV: production, ORCHESTRATOR_URL: "http://localhost:8080"}
      resources: {requests: {memory: 512Mi, cpu: "0.3"}, limits: {memory: 1Gi, cpu: "1"}}
      readinessProbe: {port: 3000, path: /login}
  endpoints:
    - name: web
      port: 3000
      public: true
$$;
