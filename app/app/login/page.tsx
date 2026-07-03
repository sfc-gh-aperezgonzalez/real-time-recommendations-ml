'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { setPlayer } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import Link from 'next/link';

interface DemoPlayer {
  PLAYER_ID: string;
  DISPLAY_NAME: string;
  PLAYER_SEGMENT: string;
  REGION_CODE: string;
}

export default function LoginPage() {
  const router = useRouter();
  const [demoPlayers, setDemoPlayers] = useState<DemoPlayer[]>([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiGet<{ players: DemoPlayer[] }>('/api/demo-players')
      .then((data) => setDemoPlayers(data.players || []))
      .catch(() => {});
  }, []);

  const handleDemoLogin = (player: DemoPlayer) => {
    setPlayer({
      player_id: player.PLAYER_ID,
      display_name: player.DISPLAY_NAME,
      region_code: player.REGION_CODE,
      player_segment: player.PLAYER_SEGMENT,
    });
    router.push('/');
  };

  const handleEmailLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await apiPost<{ player_id: string; display_name: string; region_code: string; error?: string }>(
        '/api/login',
        { email, password }
      );
      if (data.error) {
        setError(data.error);
      } else {
        setPlayer({
          player_id: data.player_id,
          display_name: data.display_name,
          region_code: data.region_code,
        });
        router.push('/');
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login-page">
      <div className="login-container">
        <div className="login-hero">
          <div className="logo-large">
            <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
              <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="#7A3FF2" stroke="#F2A03F" strokeWidth="1"/>
            </svg>
            PlayNova
          </div>
          <p className="tagline">Real-time personalized game recommendations</p>
        </div>

        <div className="form-container">
          <h1>Sign In</h1>
          <p>Use your credentials or pick a demo player below.</p>

          <form onSubmit={handleEmailLogin}>
            <div className="form-group">
              <label htmlFor="email">Email</label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@example.com"
                required
              />
            </div>
            <div className="form-group">
              <label htmlFor="password">Password</label>
              <input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                required
              />
            </div>
            {error && <p className="form-error">{error}</p>}
            <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} disabled={loading}>
              {loading ? 'Signing in…' : 'Sign In'}
            </button>
          </form>

          <p style={{ marginTop: 16, textAlign: 'center', fontSize: '0.85rem' }}>
            Don&apos;t have an account? <Link href="/register">Register</Link>
          </p>

          <div className="demo-players-section">
            <h3>Quick Demo Login</h3>
            <p>Click a player to instantly explore recommendations.</p>
            <div className="demo-players-grid">
              {demoPlayers.map((p) => (
                <div key={p.PLAYER_ID} className="demo-player-card" onClick={() => handleDemoLogin(p)}>
                  <div className="name">{p.DISPLAY_NAME}</div>
                  <div className="meta">
                    <span className="segment">{p.PLAYER_SEGMENT}</span> &middot; {p.REGION_CODE}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
