'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { getPlayer, setLastPlayed, setLastPlayedCategory } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import Nav from '@/components/Nav';
import Link from 'next/link';

interface GameDetail {
  GAME_TITLE_ID: string;
  GAME_TITLE: string;
  STUDIO_NAME: string;
  GAME_DESCRIPTION: string;
  TILE_IMAGE_URL: string;
  TILE_COLOR_HEX: string;
  RETURN_TO_PLAYER_PCT: number;
  CATEGORY_ID: string;
  CATEGORY_NAME: string;
  VERTICAL: string;
}

export default function GamePage() {
  const params = useParams();
  const router = useRouter();
  const gameId = params.id as string;
  const [game, setGame] = useState<GameDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [toast, setToast] = useState('');

  useEffect(() => {
    if (!getPlayer()) {
      router.push('/login');
      return;
    }
    apiGet<GameDetail>(`/api/game/${gameId}`)
      .then(setGame)
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [gameId, router]);

  const showToast = (msg: string) => {
    setToast(msg);
    setTimeout(() => setToast(''), 4000);
  };

  const firePlay = async (rounds: number = 1) => {
    const player = getPlayer();
    if (!player || !game) return;
    for (let i = 0; i < rounds; i++) {
      await apiPost('/api/events', {
        player_id: player.player_id,
        event_type: 'PLAY',
        game_title_id: game.GAME_TITLE_ID,
        category_id: game.CATEGORY_ID,
        region_code: player.region_code,
        stake_amt: 1.0,
      });
    }
    setLastPlayed(game.GAME_TITLE_ID);
    setLastPlayedCategory(game.CATEGORY_ID);
    showToast(`Playing${rounds > 1 ? ` (${rounds} rounds)` : ''}… your recommendations will update`);
  };

  if (loading) {
    return (
      <>
        <Nav />
        <div className="loading">Loading game…</div>
      </>
    );
  }

  if (!game) {
    return (
      <>
        <Nav />
        <div className="page">
          <p>Game not found.</p>
          <Link href="/">Back to home</Link>
        </div>
      </>
    );
  }

  const color = game.TILE_COLOR_HEX || '#7A3FF2';

  return (
    <>
      <Nav />
      <div className="page">
        <div
          className="game-hero"
          style={{
            background: `linear-gradient(135deg, ${color} 0%, ${color}66 50%, var(--bg-primary) 100%)`,
          }}
        >
          <div className="game-hero-content">
            <h1>{game.GAME_TITLE}</h1>
            <p className="studio">by {game.STUDIO_NAME}</p>
            <div className="meta-row">
              <span className="meta-tag">{game.CATEGORY_NAME}</span>
              <span className="meta-tag">{game.VERTICAL}</span>
              {game.RETURN_TO_PLAYER_PCT && (
                <span className="meta-tag">RTP {game.RETURN_TO_PLAYER_PCT}%</span>
              )}
            </div>
            {game.GAME_DESCRIPTION && (
              <p className="description">{game.GAME_DESCRIPTION}</p>
            )}
            <div className="actions">
              <button className="btn btn-accent" onClick={() => firePlay(1)}>
                ▶ Play
              </button>
              <button className="btn btn-primary" onClick={() => firePlay(3)}>
                ▶ Play 3 Rounds
              </button>
              <Link href="/" className="btn btn-secondary">
                ← Back to Home
              </Link>
            </div>
          </div>
        </div>
      </div>
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
