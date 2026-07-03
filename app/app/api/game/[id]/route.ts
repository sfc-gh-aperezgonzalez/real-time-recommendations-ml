export const dynamic = "force-dynamic";
import { NextRequest, NextResponse } from 'next/server';

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';

export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const res = await fetch(`${ORCHESTRATOR_URL}/game/${params.id}`);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
