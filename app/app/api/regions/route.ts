export const dynamic = "force-dynamic";
import { NextResponse } from 'next/server';

const ORCHESTRATOR_URL = process.env.ORCHESTRATOR_URL || 'http://localhost:8080';

export async function GET() {
  const res = await fetch(`${ORCHESTRATOR_URL}/regions`);
  const data = await res.json();
  return NextResponse.json(data, { status: res.status });
}
