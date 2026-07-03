export const dynamic = "force-dynamic";
import { NextRequest, NextResponse } from 'next/server';

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';

export async function POST(request: NextRequest) {
  const body = await request.json();
  const res = await fetch(`${ORCHESTRATOR_URL}/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
