'use client';

import Link from 'next/link';
import { useState } from 'react';
import { categoryColor } from '@/lib/categories';

interface GameCardProps {
  game_title_id: string;
  title: string;
  studio: string;
  category: string;
  category_id?: string | number;
  vertical?: string;
  tile_url: string;
  tile_color: string;
  score: number;
  boosted?: boolean;
  trend_score?: number;
  rail?: string;
  region?: string;
}

export default function GameCard({
  game_title_id,
  title,
  studio,
  category,
  category_id,
  tile_url,
  tile_color,
  score,
  boosted,
  trend_score,
  rail,
  region,
}: GameCardProps) {
  const [imgError, setImgError] = useState(false);
  const imgSrc = tile_url ? `/${tile_url}` : '';
  const catColor = categoryColor(category_id);
  const isTrending = rail === 'trending_in_market';
  // Match score is a 0-1 likelihood; clamp defensively so a badge never shows >100%.
  const matchPct = Math.min(100, Math.max(0, Math.round(score * 100)));

  return (
    <Link href={`/game/${game_title_id}`} className="game-card">
      <div className="game-card-stripe" style={{ background: catColor }} />
      <div className="game-card-image">
        {!imgError && imgSrc ? (
          <img
            src={imgSrc}
            alt={title}
            onError={() => setImgError(true)}
          />
        ) : (
          <div
            className="game-card-fallback"
            style={{
              background: `linear-gradient(135deg, ${tile_color || catColor}, ${tile_color || catColor}88)`,
            }}
          >
            {title.charAt(0)}
          </div>
        )}
      </div>
      <div className="game-card-body">
        <div className="game-card-title">{title}</div>
        <div className="game-card-studio">{studio}</div>
        <div className="game-card-footer">
          <span
            className="game-card-badge"
            style={{ background: `${catColor}26`, color: catColor, borderColor: `${catColor}66` }}
          >
            {category}
          </span>
          {isTrending ? (
            <span className="game-card-trend" title={`Popularity in ${region || 'your market'}`}>
              🔥 Popular{typeof trend_score === 'number' ? ` ${trend_score}` : ''}
            </span>
          ) : (
            <span
              className={`game-card-score${boosted ? ' game-card-score--boosted' : ''}`}
              title={boosted ? 'Boosted by your live play' : 'Model match likelihood'}
            >
              {boosted ? '⚡ ' : ''}{matchPct}%
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
