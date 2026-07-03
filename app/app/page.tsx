'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getPlayer, getLastPlayed } from '@/lib/session';
import { apiPost } from '@/lib/api';
import Nav from '@/components/Nav';
import GameCard from '@/components/GameCard';
import { categoryColor } from '@/lib/categories';

interface Card {
  game_title_id: string;
  title: string;
  studio: string;
  category: string;
  category_id?: string | number;
  vertical: string;
  tile_url: string;
  tile_color: string;
  score: number;
  boosted?: boolean;
  trend_score?: number;
  rail: string;
}

interface RecommendationsResponse {
  player_id: string;
  region_code: string;
  trace_id: string;
  candidate_set_size: number;
  excluded_count: number;
  latency_ms: { total: number };
  rails: {
    recommended_for_you: Card[];
    trending_in_market: Card[];
    because_you_played?: Card[];
  };
}

export default function HomePage() {
  const router = useRouter();
  const [recs, setRecs] = useState<RecommendationsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [catFilter, setCatFilter] = useState<string | null>(null);

  const fetchRecs = async () => {
    const player = getPlayer();
    if (!player) {
      router.push('/login');
      return;
    }
    setLoading(true);
    try {
      const lastPlayed = getLastPlayed();
      const body: Record<string, unknown> = {
        player_id: player.player_id,
        region_code: player.region_code,
        top_n: 12,
      };
      if (lastPlayed) body.because_you_played = lastPlayed;
      const data = await apiPost<RecommendationsResponse>('/api/recommendations', body);
      setRecs(data);
    } catch {
      // silently handle
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const player = getPlayer();
    if (!player) {
      router.push('/login');
      return;
    }
    fetchRecs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (loading && !recs) {
    return (
      <>
        <Nav />
        <div className="loading">Loading recommendations…</div>
      </>
    );
  }

  const railLabels: Record<string, string> = {
    recommended_for_you: 'Recommended for you',
    trending_in_market: 'Trending in your market',
    because_you_played: 'Because you played',
  };

  // Distinct categories present across all rails, for the filter chip bar.
  const allCards = recs ? Object.values(recs.rails).flat().filter(Boolean) as Card[] : [];
  const categories = Array.from(
    new Map(
      allCards
        .filter((c) => c.category_id != null)
        .map((c) => [String(c.category_id), c.category]),
    ).entries(),
  ).sort((a, b) => Number(a[0]) - Number(b[0]));
  const matchesFilter = (c: Card) => !catFilter || String(c.category_id) === catFilter;

  return (
    <>
      <Nav />
      <div className="page">
        {recs && (
          <div className="stats-strip">
            <div className="stat">
              Candidates: <strong>{recs.candidate_set_size}</strong>
            </div>
            <div className="stat">
              Excluded: <strong>{recs.excluded_count}</strong>
            </div>
            <div className="stat">
              Latency: <strong>{recs.latency_ms?.total ?? '—'}ms</strong>
            </div>
            <div className="stat">
              Trace: <strong style={{ fontSize: '0.7rem' }}>{recs.trace_id}</strong>
            </div>
            <button className="btn btn-secondary" onClick={fetchRecs} disabled={loading}>
              {loading ? 'Refreshing…' : '↻ Refresh'}
            </button>
          </div>
        )}

        {recs && categories.length > 1 && (
          <div className="cat-filter">
            <button
              className={`cat-chip${catFilter === null ? ' cat-chip--active' : ''}`}
              onClick={() => setCatFilter(null)}
            >
              All
            </button>
            {categories.map(([id, name]) => {
              const color = categoryColor(id);
              const active = catFilter === id;
              return (
                <button
                  key={id}
                  className={`cat-chip${active ? ' cat-chip--active' : ''}`}
                  onClick={() => setCatFilter(active ? null : id)}
                  style={{
                    borderColor: color,
                    background: active ? color : `${color}1f`,
                    color: active ? '#140826' : color,
                  }}
                >
                  {name}
                </button>
              );
            })}
          </div>
        )}

        {recs &&
          Object.entries(recs.rails).map(([key, cards]) => {
            if (!cards || cards.length === 0) return null;
            const visible = (cards as Card[]).filter(matchesFilter);
            if (visible.length === 0) return null;
            return (
              <div key={key} className="rail">
                <h2>{railLabels[key] || key}</h2>
                <div className="rail-scroll">
                  {visible.map((card) => (
                    <GameCard
                      key={card.game_title_id}
                      game_title_id={card.game_title_id}
                      title={card.title}
                      studio={card.studio}
                      category={card.category}
                      category_id={card.category_id}
                      vertical={card.vertical}
                      tile_url={card.tile_url}
                      tile_color={card.tile_color}
                      score={card.score}
                      boosted={card.boosted}
                      trend_score={card.trend_score}
                      rail={card.rail}
                      region={recs.region_code}
                    />
                  ))}
                </div>
              </div>
            );
          })}
      </div>
    </>
  );
}
