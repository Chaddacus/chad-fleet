import type { FleetStateResponse, ToolSnapshot } from '@/lib/types';

/** Data access for the Tools feature. Reads the snapshot's `tools` slice (MCP registry). */
export async function getTools(): Promise<{ tools: ToolSnapshot[]; error?: string }> {
  try {
    const baseUrl = process.env.NEXT_PUBLIC_APP_URL ?? 'http://localhost:3000';
    const res = await fetch(`${baseUrl}/api/state`, { cache: 'no-store' });
    if (!res.ok) return { tools: [], error: `HTTP ${res.status}` };
    const data = (await res.json()) as FleetStateResponse;
    return { tools: data.tools ?? [], error: data.error };
  } catch (err) {
    return { tools: [], error: String(err) };
  }
}
