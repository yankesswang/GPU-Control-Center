export interface GPU { name: string; utilization: number; memory_used_mb: number; memory_total_mb: number; temperature_c: number | null; power_w: number | null }
export interface Node { id: string; name: string; mode: 'demo' | 'host'; status: string; maintenance: boolean; last_seen: string; capabilities: string[]; metrics: {simulated?: boolean; cpu_percent?: number; ram_percent?: number; ram_total_gb?: number; gpus?: GPU[]} }
export interface Job { id: string; name: string; source: string; model: string; priority: number; steps: number; status: string; progress: number; node_id: string | null; created_at: string; started_at: string | null; finished_at: string | null; error: string | null; result: Record<string, unknown> | null; events?: {id: number; message: string; created_at: string}[] }
export interface Model { id: string; name: string; runtime: string; description: string; capability: string }
export interface Overview {jobs: Record<string, number>; total: number; online_nodes: number}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, { method, headers: body === undefined ? {} : {'Content-Type': 'application/json'}, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(12000) });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : `請求失敗 (${response.status})`);
  }
  return response.json();
}

export interface Explanation {summary: string; reason: string; queue_rank: number | null; queue_size: number; candidates: {node_id: string; name: string; ready: boolean; blockers: string[]; holders: {id: string; name: string; source: string}[]}[]}
