export const dynamic = "force-dynamic";
import { NextRequest, NextResponse } from 'next/server';

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';

export async function GET(request: NextRequest) {
  const { searchParams } = new URL(request.url);
  const playerId = searchParams.get('player_id') || '';
  const limit = searchParams.get('limit') || '8';
  const res = await fetch(
    `${ORCHESTRATOR_URL}/telemetry?player_id=${encodeURIComponent(playerId)}&limit=${encodeURIComponent(limit)}`,
    { cache: 'no-store' },
  );
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
