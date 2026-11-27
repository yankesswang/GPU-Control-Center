export interface GPU { index?: number; uuid?: string; name: string; utilization: number | null; memory_used_mb: number | null; memory_total_mb: number | null; temperature_c: number | null; power_w: number | null }
export interface LoadedModel { instance_id?: string; name: string; size_bytes: number | null; vram_bytes: number | null }
export interface Node { id: string; name: string; mode: 'demo' | 'host'; default_runtime: 'lmstudio'|'ollama'; status: string; maintenance: boolean; last_seen: string; capabilities: string[]; model_catalog?: Model[]; metrics: {simulated?: boolean; cpu_percent?: number; ram_percent?: number; ram_total_gb?: number; gpus?: GPU[]; ollama?: RuntimeStatus; lmstudio?: RuntimeStatus; comfyui?: ComfyStatus} }
export interface Job { id: string; name: string; source: string; model: string; priority: number; steps: number; status: string; progress: number; node_id: string | null; created_at: string; started_at: string | null; finished_at: string | null; error: string | null; parameters: {prompt?:string; max_tokens?:number; model_action?:'load'|'unload'; target_node_id?:string}; runtime_state: {phase?:string; text?:string; thinking?:string; tokens_generated?:number; chunks_received?:number}; result: Record<string, unknown> | null; events?: {id: number; message: string; created_at: string}[] }
export interface RuntimeStatus {status: string; models: number; loaded_models_status?: string; loaded_models?: LoadedModel[] | null}
export interface ComfyStatus {status: string; version?:string; queue_status:string; models_status:string; running?:{id:string}[]; pending?:{id:string}[]; model_groups?:{folder:string;status:string;count?:number;files:string[];truncated?:boolean}[]; devices?:{name:string;vram_total:number;vram_free:number}[]}
export interface Model { model_type?: string; id: string; name: string; runtime: string; description: string; capability: string; available: boolean; nodes: string[] }
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
