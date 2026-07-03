'use client';

import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getPlayer } from '@/lib/session';
import { apiGet } from '@/lib/api';
import Nav from '@/components/Nav';

interface Latency {
  resolve: number | null;
  recent_activity: number | null;
  assemble: number | null;
  rank: number | null;
  total: number | null;
}

interface Trace {
  trace_id: string;
  request_ts: string;
  region_code: string;
  page_context: string;
  candidate_set_size: number;
  excluded_count: number;
  scoring: string | null;
  feature_source: string | null;
  model_version: string | null;
  latency: Latency;
  exclusions_by_reason: Record<string, number>;
}

interface TelemetryResponse {
  player_id: number;
  traces: Trace[];
}

const STAGES: { key: keyof Latency; label: string; component: string; color: string }[] = [
  { key: 'resolve', label: 'Region resolve', component: 'Snowflake SQL', color: '#b8a4d6' },
  { key: 'recent_activity', label: 'Recent-category read', component: 'Online Feature Store · Postgres', color: 'var(--accent-secondary)' },
  { key: 'assemble', label: 'Eligibility + policy + feature assembly', component: 'Snowflake SQL', color: 'var(--accent-primary)' },
  { key: 'rank', label: 'Model inference', component: 'SPCS · PLAYNOVA_RANKER_SVC', color: 'var(--accent-success)' },
];

// The recent-category read is served from the Online Feature Store (managed
// Postgres) when the online path is live, and falls back to the raw event stream
// in Snowflake otherwise. Label it truthfully from the trace's feature_source so
// the waterfall never claims Postgres when it actually read SQL.
function stageComponent(key: keyof Latency, featureSource: string | null): string {
  const base = STAGES.find((s) => s.key === key)?.component ?? '';
  if (key !== 'recent_activity') return base;
  return featureSource === 'online'
    ? 'Online Feature Store · Postgres'
    : 'Snowflake SQL · raw event stream (fallback)';
}


function ms(v: number | null | undefined): number {
  return typeof v === 'number' && isFinite(v) ? v : 0;
}

function Waterfall({ latency, featureSource }: { latency: Latency; featureSource: string | null }) {
  const stageMs = STAGES.map((s) => ms(latency[s.key]));
  const sumStages = stageMs.reduce((a, b) => a + b, 0);
  const total = Math.max(ms(latency.total), sumStages) || 1;
  let cursor = 0;
  const rows = STAGES.map((s, i) => {
    const dur = stageMs[i];
    const offsetPct = (cursor / total) * 100;
    const widthPct = Math.max((dur / total) * 100, 0.6);
    cursor += dur;
    return { ...s, dur, offsetPct, widthPct };
  });
  const overhead = Math.max(ms(latency.total) - sumStages, 0);
  return (
    <div className="wf">
      {rows.map((r) => (
        <div key={r.key} className="wf-row">
          <div className="wf-meta">
            <span className="wf-label">{r.label}</span>
            <span className="wf-comp">{stageComponent(r.key, featureSource)}</span>
          </div>
          <div className="wf-track">
            <div
              className="wf-bar"
              style={{ marginLeft: `${r.offsetPct}%`, width: `${r.widthPct}%`, background: r.color }}
            />
          </div>
          <span className="wf-ms">{r.dur.toFixed(1)} ms</span>
        </div>
      ))}
      <div className="wf-foot">
        <span>
          Response assembly + persistence: <strong>{overhead.toFixed(1)} ms</strong>
        </span>
        <span>
          End-to-end total: <strong>{ms(latency.total).toFixed(1)} ms</strong>
        </span>
      </div>
    </div>
  );
}

export default function TelemetryPage() {
  const router = useRouter();
  const [data, setData] = useState<TelemetryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchTelemetry = useCallback(async () => {
    const player = getPlayer();
    if (!player) {
      router.push('/login');
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet<TelemetryResponse>(
        `/api/telemetry?player_id=${player.player_id}&limit=8`,
      );
      setData(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'failed to load telemetry');
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    fetchTelemetry();
  }, [fetchTelemetry]);

  const traces = data?.traces ?? [];
  const latest = traces[0];

  return (
    <>
      <Nav />
      <div className="page">
        <div className="tele-header">
          <div>
            <h1>Request Telemetry</h1>
            <p className="tele-sub">
              Every recommendation is logged to <code>APP.RECOMMENDATION_TRACE</code>. This page reads
              it live — the real call stack across the Snowflake components for each request.
            </p>
          </div>
          <button className="btn btn-secondary" onClick={fetchTelemetry} disabled={loading}>
            {loading ? 'Refreshing…' : '↻ Refresh'}
          </button>
        </div>

        {error && <div className="tele-empty">Couldn&apos;t load telemetry: {error}</div>}

        {!error && !latest && !loading && (
          <div className="tele-empty">
            No requests yet for this player. Open your <strong>Home</strong> page to generate a
            recommendation, then come back.
          </div>
        )}

        {latest && (
          <div className="tele-card">
            <div className="tele-badges">
              <span className={`badge ${latest.scoring === 'model' ? 'badge-success' : 'badge-warn'}`}>
                scoring: {latest.scoring ?? 'n/a'}
              </span>
              <span className="badge">model: {latest.model_version ?? 'n/a'}</span>
              <span className="badge">features: {latest.feature_source ?? 'n/a'}</span>
              <span className="badge badge-total">{ms(latest.latency.total).toFixed(0)} ms total</span>
            </div>

            <Waterfall latency={latest.latency} featureSource={latest.feature_source} />

            <div className="tele-chips">
              <span className="chip">Candidates <strong>{latest.candidate_set_size}</strong></span>
              <span className="chip">Excluded by policy <strong>{latest.excluded_count}</strong></span>
              <span className="chip">Market <strong>{latest.region_code}</strong></span>
              <span className="chip">Context <strong>{latest.page_context}</strong></span>
              <span className="chip">At <strong>{latest.request_ts.slice(0, 19)}</strong></span>
              <span className="chip chip-mono">trace {latest.trace_id.slice(0, 8)}…</span>
            </div>

            {Object.keys(latest.exclusions_by_reason || {}).length > 0 && (
              <div className="tele-excl">
                <span className="tele-excl-title">Policy suppressions (before ML):</span>
                {Object.entries(latest.exclusions_by_reason).map(([reason, n]) => (
                  <span key={reason} className="chip">
                    {reason} <strong>{n}</strong>
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {traces.length > 1 && (
          <div className="tele-history">
            <h2>Recent requests</h2>
            <table className="tele-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Total</th>
                  <th>Feature read</th>
                  <th>Assembly</th>
                  <th>Inference</th>
                  <th>Scoring</th>
                  <th>Candidates</th>
                </tr>
              </thead>
              <tbody>
                {traces.map((t) => (
                  <tr key={t.trace_id}>
                    <td>{t.request_ts.slice(0, 19)}</td>
                    <td>{ms(t.latency.total).toFixed(0)} ms</td>
                    <td>{ms(t.latency.recent_activity).toFixed(0)} ms</td>
                    <td>{ms(t.latency.assemble).toFixed(0)} ms</td>
                    <td>{ms(t.latency.rank).toFixed(0)} ms</td>
                    <td>
                      <span className={`badge ${t.scoring === 'model' ? 'badge-success' : 'badge-warn'}`}>
                        {t.scoring ?? 'n/a'}
                      </span>
                    </td>
                    <td>{t.candidate_set_size}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
