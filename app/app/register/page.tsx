'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { setPlayer } from '@/lib/session';
import { apiGet, apiPost } from '@/lib/api';
import Link from 'next/link';

interface Region {
  REGION_CODE: string;
  REGION_NAME: string;
}

export default function RegisterPage() {
  const router = useRouter();
  const [regions, setRegions] = useState<Region[]>([]);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [regionCode, setRegionCode] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    apiGet<{ regions: Region[] }>('/api/regions')
      .then((data) => {
        setRegions(data.regions || []);
        if (data.regions?.length) setRegionCode(data.regions[0].REGION_CODE);
      })
      .catch(() => {});
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    try {
      const data = await apiPost<{ player_id: string; display_name: string; region_code: string; error?: string }>(
        '/api/register',
        { email, password, region_code: regionCode, display_name: displayName || undefined }
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
      setError(err instanceof Error ? err.message : 'Registration failed');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="form-page">
      <div className="form-container">
        <h1>Create Account</h1>
        <p>Join PlayNova and get personalized game recommendations.</p>

        <form onSubmit={handleSubmit}>
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
              placeholder="Choose a password"
              required
            />
          </div>
          <div className="form-group">
            <label htmlFor="region">Region</label>
            <select
              id="region"
              value={regionCode}
              onChange={(e) => setRegionCode(e.target.value)}
              required
            >
              {regions.map((r) => (
                <option key={r.REGION_CODE} value={r.REGION_CODE}>
                  {r.REGION_NAME} ({r.REGION_CODE})
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label htmlFor="displayName">Display Name (optional)</label>
            <input
              id="displayName"
              type="text"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Your gamer tag"
            />
          </div>
          {error && <p className="form-error">{error}</p>}
          <button type="submit" className="btn btn-primary" style={{ width: '100%', marginTop: 12 }} disabled={loading}>
            {loading ? 'Creating…' : 'Create Account'}
          </button>
        </form>

        <p style={{ marginTop: 16, textAlign: 'center', fontSize: '0.85rem' }}>
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </div>
    </div>
  );
}
