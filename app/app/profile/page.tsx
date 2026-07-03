'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getPlayer, Player } from '@/lib/session';
import Nav from '@/components/Nav';

export default function ProfilePage() {
  const router = useRouter();
  const [player, setPlayerState] = useState<Player | null>(null);

  useEffect(() => {
    const p = getPlayer();
    if (!p) {
      router.push('/login');
      return;
    }
    setPlayerState(p);
  }, [router]);

  if (!player) return null;

  return (
    <>
      <Nav />
      <div className="page">
        <div className="profile-card">
          <h1>Player Profile</h1>
          <div className="profile-field">
            <span className="label">Player ID</span>
            <span className="value">{player.player_id}</span>
          </div>
          <div className="profile-field">
            <span className="label">Display Name</span>
            <span className="value">{player.display_name}</span>
          </div>
          {player.player_segment && (
            <div className="profile-field">
              <span className="label">Segment</span>
              <span className="value">{player.player_segment}</span>
            </div>
          )}
          <div className="profile-field">
            <span className="label">Region</span>
            <span className="value">{player.region_code}</span>
          </div>
          <div className="profile-note">
            <strong>How recommendations personalize:</strong> Every game you play sends a real-time
            event that instantly updates your player profile. The recommendation engine uses your
            play history, favorite categories, session recency, and market trends to rank games
            uniquely for you. Try playing a few games and watch your &quot;Recommended for you&quot;
            rail change in real time.
          </div>
        </div>
      </div>
    </>
  );
}
