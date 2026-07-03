'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { getPlayer, clearPlayer } from '@/lib/session';

export default function Nav() {
  const router = useRouter();
  const player = getPlayer();

  const handleLogout = () => {
    clearPlayer();
    router.push('/login');
  };

  return (
    <nav className="nav">
      <Link href="/" className="nav-logo">
        <svg viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
          <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z" fill="#7A3FF2" stroke="#F2A03F" strokeWidth="1"/>
        </svg>
        PlayNova
      </Link>
      <div className="nav-links">
        <Link href="/">Home</Link>
        <Link href="/profile">Profile</Link>
        <Link href="/telemetry">Telemetry</Link>
      </div>
      <div className="nav-player">
        {player && (
          <>
            <div className="nav-chip">
              <span>{player.display_name}</span>
              <span className="region">{player.region_code}</span>
            </div>
            <button className="btn btn-secondary" onClick={handleLogout}>
              Logout
            </button>
          </>
        )}
      </div>
    </nav>
  );
}
