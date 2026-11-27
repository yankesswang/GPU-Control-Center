export interface ComfyActiveModel {
  name: string;
  role: 'diffusion' | 'checkpoint' | 'text_encoder' | 'lora' | 'vae' | 'controlnet' | 'clip_vision' | 'upscale' | 'other' | string;
  folder?: string;
  gpu_index?: number | null;
  device?: string | null;
  vram_bytes?: number | null;
  total_bytes?: number | null;
  offloaded_bytes?: number | null;
  ram_bytes?: number | null;
  is_dynamic?: boolean;
  currently_used?: boolean;
  memory_source?: 'comfyui_internal' | string;
}
export interface GpuProcess { pid: number | null; label: string; category: 'comfyui' | 'lmstudio' | 'ollama' | 'external' | string; memory_used_mb: number | null; models?: ComfyActiveModel[] }
export interface GPU { index?: number; uuid?: string; name: string; utilization: number | null; memory_used_mb: number | null; memory_total_mb: number | null; temperature_c: number | null; power_w: number | null; other_processes?: GpuProcess[] }
export interface ObservedGpu { index: number; memory_used_mb: number | null }
export interface ConfiguredPlacement { mode: 'pinned' | 'auto' | 'cpu'; gpu_indices: number[]; main_gpu?: number | null; disabled_gpus?: number[]; split_strategy?: string | null; ratio?: number | string | null }
export interface LoadedModel { instance_id?: string; name: string; size_bytes: number | null; vram_bytes: number | null; configured_placement?: ConfiguredPlacement | null; observed_gpus?: ObservedGpu[]; observed_status?: 'observed' | 'cpu_only' | 'unmatched' | 'ambiguous' | 'unavailable' }
export interface Node { connection_host?:string|null; connection_services?:Record<string,string>; auth_required?:boolean; auth_unlocked?:boolean; auth_unlock_expires_at?:string|null; id: string; name: string; mode: 'demo' | 'host'; default_runtime: 'lmstudio'|'ollama'; model_aliases?: Record<string, string>; hidden_models?: string[]; status: string; maintenance: boolean; last_seen: string; capabilities: string[]; model_catalog?: Model[]; metrics: {simulated?: boolean; cpu_percent?: number; ram_percent?: number; ram_total_gb?: number; gpus?: GPU[]; ollama?: RuntimeStatus; lmstudio?: RuntimeStatus; comfyui?: ComfyStatus} }
export type SlaClass = 'realtime' | 'interactive' | 'standard' | 'batch' | 'background';
export interface Job { id: string; name: string; source: string; model: string; priority: number; steps: number; status: string; progress: number; node_id: string | null; created_at: string; started_at: string | null; finished_at: string | null; error: string | null; parameters: {prompt?:string; max_tokens?:number; model_action?:'load'|'unload'; target_node_id?:string; gpu_indices?:number[]; replace?:boolean; sla_class?:SlaClass; deadline_seconds?:number; preferred_node_id?:string; fallback_models?:string[]; allow_defer?:boolean; original_model?:string}; runtime_state: {phase?:string; text?:string; thinking?:string; tokens_generated?:number; chunks_received?:number}; result: Record<string, unknown> | null; events?: {id: number; message: string; created_at: string}[] }
export interface RuntimeStatus {status: string; backend?: 'lmstudio_app' | 'direct_llama' | 'disk_only'; catalog_source?: 'api' | 'disk'; models_dir?: string; models: number; gpu_placement_supported?: boolean; loaded_models_status?: string; loaded_models?: LoadedModel[] | null}
export interface ComfyStatus {status: string; version?:string; catalog_source?: 'api' | 'disk'; models_dir?: string; gpu_placement_supported?:boolean; loaded_models_telemetry?:string; loaded_models?:ComfyActiveModel[]; queue_status:string; models_status:string; running?:{id:string}[]; pending?:{id:string}[]; active_models?:ComfyActiveModel[]; active_models_source?:string; model_groups?:{folder:string;status:string;count?:number;files:string[];truncated?:boolean}[]; devices?:{name:string;vram_total:number;vram_free:number}[]}
export interface Model { model_type?: string; display_name?: string; hidden?: boolean; id: string; name: string; runtime: string; description: string; capability: string; available: boolean; nodes: string[] }
export const modelDisplayName = (node: Node, modelId: string, fallback: string) => node.model_aliases?.[modelId]?.trim() || fallback;
export function formatBytes(bytes?: number | null): string {
  if (bytes == null || !Number.isFinite(bytes)) return '—';
  if (bytes >= 1024 ** 3) return `${(bytes / 1024 ** 3).toFixed(2)} GiB`;
  if (bytes >= 1024 ** 2) return `${Math.round(bytes / 1024 ** 2)} MiB`;
  if (bytes > 0) return '< 1 MiB';
  return '0 MiB';
}
export function comfyRoleLabel(role: string): string {
  switch (role) {
    case 'diffusion': return '主模型';
    case 'checkpoint': return 'Checkpoint';
    case 'text_encoder': return '文字編碼';
    case 'lora': return 'LoRA';
    case 'vae': return 'VAE';
    case 'controlnet': return 'ControlNet';
    case 'clip_vision': return 'CLIP Vision';
    case 'upscale': return '放大模型';
    default: return '模型';
  }
}
export interface Overview {jobs: Record<string, number>; total: number; online_nodes: number}
export class ApiError extends Error {
  status: number;
  authRequired: boolean;
  constructor(message: string, status: number, authRequired = false) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.authRequired = authRequired;
  }
}
export async function api<T>(path: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method,
    credentials: 'same-origin',
    headers: body === undefined ? {} : {'Content-Type': 'application/json'},
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(12000),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const message = typeof error.detail === 'string' ? error.detail : `Request failed (${response.status})`;
    const authRequired = response.status === 403 && response.headers.get('X-Host-Auth-Required') === 'true';
    throw new ApiError(message, response.status, authRequired);
  }
  return response.json();
}

export interface ExplanationCandidate {
  node_id: string;
  name: string;
  ready: boolean;
  blockers: string[];
  holders: { id: string; name: string; source: string; status?: string }[];
  score?: number;
  warm_hit?: boolean;
  health_score?: number;
  vram_used_pct?: number | null;
  max_temp_c?: number | null;
  recommended?: boolean;
  effective_model?: string;
  used_fallback?: boolean;
  score_breakdown?: Record<string, number>;
}

export interface Explanation {
  summary: string;
  reason: string;
  decision_action?: string;
  sla_class?: SlaClass;
  queue_rank: number | null;
  queue_size: number;
  recommended_node_id?: string | null;
  recommended_score?: number | null;
  policy?: string;
  policy_mode?: string;
  tariff_tier?: string;
  fairness?: {
    source: string;
    weight: number;
    active_jobs: number;
    queued_jobs: number;
    recent_compute_s: number;
    fairness_pressure: number;
  } | null;
  candidates: ExplanationCandidate[];
}

export type SchedulerPolicyMode = 'balanced' | 'latency_first' | 'fair_share' | 'throughput_warm' | 'thermal_eco';

export interface SchedulerPolicyPreset {
  label_zh: string;
  label_en: string;
  description_zh: string;
  description_en: string;
  weights: Record<string, number | boolean>;
}

export interface SchedulerPolicy {
  id: string;
  mode: SchedulerPolicyMode;
  weight_priority: number;
  weight_aging: number;
  weight_deadline: number;
  weight_fairness: number;
  weight_warm_affinity: number;
  weight_node_health: number;
  weight_sjf: number;
  aging_interval_s: number;
  fairness_window_s: number;
  max_queued_per_source: number;
  source_weights: Record<string, number>;
  thermal_soft_c: number;
  thermal_hard_c: number;
  vram_guard_pct: number;
  comfyui_busy_penalty: number;
  defer_batch_on_peak_tariff: boolean;
  route_reservation_s: number;
  routing_margin: number;
  updated_at?: string;
  presets?: Record<SchedulerPolicyMode, SchedulerPolicyPreset>;
}

export interface SchedulerNodeState {
  node_id: string;
  name: string;
  mode: string;
  online: boolean;
  maintenance: boolean;
  ready_slot: boolean;
  default_runtime: string;
  gpu_count: number;
  avg_util_pct: number | null;
  max_util_pct: number | null;
  max_temp_c: number | null;
  vram_used_pct: number | null;
  free_vram_mb: number | null;
  total_vram_mb: number | null;
  power_w: number | null;
  comfyui_running: number;
  comfyui_pending: number;
  external_gpu_vram_mb: number;
  warm_models: string[];
  health_score: number;
  thermal_hard_blocked: boolean;
  vram_pressure_high: boolean;
  health_notes: string[];
  active_holders: { id: string; name: string; source: string; model: string; status: string }[];
}

export interface SchedulerServiceUsage {
  source: string;
  weight: number;
  active_jobs: number;
  queued_jobs: number;
  recent_completed_jobs: number;
  recent_compute_s: number;
  weighted_load: number;
  fairness_pressure: number;
}

export interface SchedulerQueuePlanItem {
  rank: number;
  job_id: string;
  name: string;
  source: string;
  model: string;
  effective_model: string;
  used_fallback: boolean;
  priority: number;
  sla_class: SlaClass;
  wait_seconds: number;
  deadline_seconds: number | null;
  decision_action: string;
  recommended_node_id: string | null;
  recommended_node_name: string | null;
  warm_hit: boolean;
  score: number;
  breakdown: Record<string, number>;
  blockers: string[];
}

export interface SchedulerDecisionItem {
  id: number;
  recorded_at: string;
  action: 'dispatch' | 'yield' | 'defer' | 'reject' | 'fallback' | string;
  job_id: string | null;
  job_name: string | null;
  source: string | null;
  model: string | null;
  sla_class: string | null;
  selected_node_id: string | null;
  selected_node_name: string | null;
  score: number | null;
  policy_mode: string;
  reason: string;
  breakdown: Record<string, unknown>;
}

export interface SchedulerState {
  generated_at: string;
  policy: SchedulerPolicy;
  cluster: {
    total_nodes: number;
    online_nodes: number;
    ready_nodes: number;
    active_jobs: number;
    queued_jobs: number;
    active_services: number;
    tariff_tier: string;
    current_rate: number;
    currency: string;
  };
  nodes: SchedulerNodeState[];
  services: SchedulerServiceUsage[];
  queue_plan: SchedulerQueuePlanItem[];
  recent_decisions: SchedulerDecisionItem[];
}

export interface SchedulerSimulationResult {
  generated_at: string;
  policy_mode: string;
  tariff_tier: string;
  admission_allowed: boolean;
  decision_action: string;
  summary: string;
  selected_node_id: string | null;
  selected_node_name: string | null;
  selected_model: string;
  used_fallback: boolean;
  selected_score: number | null;
  candidates: {
    node_id: string;
    node_name: string;
    runnable: boolean;
    recommended: boolean;
    effective_model: string;
    used_fallback: boolean;
    warm_hit: boolean;
    score: number;
    health_score: number;
    vram_used_pct: number | null;
    max_temp_c: number | null;
    avg_util_pct: number | null;
    blockers: string[];
    breakdown: Record<string, number>;
  }[];
}

export type EnergyRange = '1h' | '6h' | '24h' | '7d' | '30d' | '90d' | 'all';
export type TariffMode = 'flat' | 'tou_tw' | 'tou_custom';
export type CurrencyCode = 'TWD' | 'USD' | 'EUR' | 'JPY' | 'CNY';
export type BillCategory = 'utility_bill' | 'smart_plug' | 'meter_reading' | 'other';

export interface TariffSetting {
  id: string;
  currency: CurrencyCode;
  tariff_mode: TariffMode;
  flat_rate: number;
  peak_rate: number;
  off_peak_rate: number;
  non_summer_peak_rate: number;
  non_summer_off_peak_rate: number;
  peak_start_hour: number;
  peak_end_hour: number;
  weekend_off_peak: boolean;
  summer_months: number[];
  timezone_offset_hours: number;
  pue_factor: number;
  base_host_watts: number;
  co2_kg_per_kwh: number;
  monthly_budget: number | null;
  updated_at?: string;
}

export interface ElectricityBill {
  id: string;
  billing_date: string;
  period_start: string;
  period_end: string;
  label: string;
  category: BillCategory;
  node_id: string | null;
  kwh: number;
  cost: number;
  currency: CurrencyCode;
  peak_kwh: number | null;
  off_peak_kwh: number | null;
  notes: string | null;
  created_at: string;
  unit_rate?: number;
  telemetry_kwh?: number;
  telemetry_cost?: number;
  telemetry_days?: number;
  gpu_share_pct?: number | null;
}

export interface EnergyTimeseriesPoint {
  ts: string;
  label: string;
  avg_total_w: number;
  avg_gpu_w: number;
  max_total_w: number;
  min_total_w: number;
  kwh: number;
  cost: number;
  tariff_tier: 'peak' | 'off_peak' | 'flat';
  avg_utilization: number | null;
  avg_temperature_c: number | null;
  samples: number;
}

export interface EnergyHeatmapCell {
  hour: number;
  avg_w: number;
  kwh: number;
  cost: number;
  samples: number;
  is_peak: boolean;
}

export interface EnergyGpuBreakdown {
  node_id: string;
  node_name: string;
  gpu_index: number;
  gpu_name: string;
  kwh: number;
  cost: number;
  share_pct: number;
  avg_w: number;
  max_w: number;
  avg_utilization: number | null;
  avg_temperature_c: number | null;
  idle_pct: number;
  idle_kwh: number;
  idle_cost: number;
  w_per_10pct_util: number | null;
  hours: number;
}

export interface EnergyModelBreakdown {
  model: string;
  is_idle: boolean;
  kwh: number;
  cost: number;
  share_pct: number;
  avg_w: number;
  hours: number;
  job_count: number;
  cost_per_job: number | null;
  total_tokens: number;
  cost_per_1k_tokens: number | null;
}

export interface EnergyInsight {
  id: string;
  severity: 'info' | 'positive' | 'warning';
  title_zh: string;
  title_en: string;
  body_zh: string;
  body_en: string;
  metric: string;
}

export interface EnergyAnalytics {
  range: EnergyRange;
  bucket_seconds: number;
  generated_at: string;
  tariff: TariffSetting;
  live: {
    gpu_power_w: number;
    host_overhead_w: number;
    psu_loss_w: number;
    total_power_w: number;
    current_rate: number;
    current_tier: 'peak' | 'off_peak' | 'flat';
    is_summer: boolean;
    cost_per_hour: number;
    cost_per_day: number;
    projected_month_cost_at_current: number;
    active_nodes: { node_id: string; node_name: string; gpu_power_w: number; total_power_w: number; gpu_count: number; simulated: boolean }[];
    live_gpus: { node_id: string; node_name: string; index: number; name: string; power_w: number; utilization: number | null; temperature_c: number | null; simulated: boolean }[];
  };
  summary: {
    sample_count: number;
    observed_hours: number;
    has_simulated: boolean;
    total_kwh: number;
    gpu_kwh: number;
    host_overhead_kwh: number;
    psu_loss_kwh: number;
    total_cost: number;
    peak_kwh: number;
    off_peak_kwh: number;
    peak_kwh_share_pct: number;
    peak_cost: number;
    off_peak_cost: number;
    effective_rate: number;
    flat_equivalent_cost: number;
    tou_equivalent_cost: number;
    shift_50_peak_savings: number;
    co2_kg: number;
    stats: {
      mean_w: number;
      median_w: number;
      p95_w: number;
      max_w: number;
      min_w: number;
      std_w: number;
      load_factor: number;
      avg_utilization: number | null;
      avg_temperature_c: number | null;
    };
    forecast: {
      daily_avg_kwh: number;
      daily_avg_cost: number;
      projected_30d_kwh: number;
      projected_30d_cost: number;
      projected_30d_co2_kg: number;
      monthly_budget: number | null;
      budget_utilization_pct: number | null;
      projected_days_to_budget: number | null;
    };
  };
  timeseries: EnergyTimeseriesPoint[];
  heatmap: { max_w: number; days: { day: number; hours: EnergyHeatmapCell[] }[] };
  gpu_breakdown: EnergyGpuBreakdown[];
  node_breakdown: { node_id: string; node_name: string; kwh: number; gpu_kwh: number; cost: number; share_pct: number; avg_w: number; max_w: number; hours: number; simulated: boolean }[];
  model_breakdown: EnergyModelBreakdown[];
  bill_analytics: {
    count: number;
    total_kwh: number;
    total_cost: number;
    avg_unit_rate: number;
    mom_cost_change_pct: number | null;
    mom_kwh_change_pct: number | null;
    latest_bill: ElectricityBill | null;
    bills: ElectricityBill[];
  };
  insights: EnergyInsight[];
  available_nodes: { id: string; name: string }[];
}

export type McpRole = 'read' | 'infer' | 'admin';

export interface McpToolInfo {
  name: string;
  role: McpRole;
  category: string;
  summary_zh: string;
  summary_en: string;
}

export interface McpResourceInfo {
  uri: string;
  summary_zh: string;
  summary_en: string;
}

export interface McpPromptInfo {
  name: string;
  summary_zh: string;
  summary_en: string;
}

export interface McpInfo {
  protocol: string;
  default_port: number;
  path: string;
  auth_enabled: boolean;
  auth_enforced_by_tokens: boolean;
  static_api_key_configured: boolean;
  require_auth_env: boolean;
  default_role: McpRole;
  active_tokens: number;
  total_tokens: number;
  total_requests: number;
  tools: McpToolInfo[];
  resources: McpResourceInfo[];
  prompts: McpPromptInfo[];
}

export interface McpToken {
  id: string;
  name: string;
  source_tag: string;
  role: McpRole;
  token_prefix: string;
  notes: string | null;
  request_count: number;
  last_used_at: string | null;
  created_at: string;
  revoked_at: string | null;
  active: boolean;
}

export interface McpCreatedToken extends McpToken {
  token: string;
}

export interface HardwareTimeseriesPoint {
  ts: string;
  label: string;
  avg_gpu_util: number | null;
  max_gpu_util: number | null;
  avg_vram_pct: number | null;
  max_vram_pct: number | null;
  avg_vram_gb: number | null;
  avg_temp_c: number | null;
  max_temp_c: number | null;
  avg_cpu_pct: number | null;
  avg_ram_pct: number | null;
  avg_power_w: number | null;
  avg_fan_pct: number | null;
  avg_clock_mhz: number | null;
  per_gpu_temp: Record<string, number>;
  per_gpu_util: Record<string, number>;
  samples: number;
}

export interface HardwareGpuSeriesMeta {
  key: string;
  node_id: string;
  node_name: string;
  gpu_index: number;
  label: string;
  full_label: string;
}

export interface HardwareThermalCurveBin {
  bin_start: number;
  bin_end: number;
  label: string;
  samples: number;
  avg_temp_c: number | null;
  max_temp_c: number | null;
  avg_power_w: number | null;
  avg_fan_pct: number | null;
}

export interface HardwareGpuProfile {
  node_id: string;
  node_name: string;
  gpu_index: number;
  gpu_name: string;
  health_grade: 'healthy' | 'watch' | 'hot';
  hours: number;
  samples: number;
  avg_util: number | null;
  p95_util: number | null;
  max_util: number | null;
  avg_temp_c: number | null;
  p95_temp_c: number | null;
  max_temp_c: number | null;
  avg_vram_pct: number | null;
  p95_vram_pct: number | null;
  max_vram_pct: number | null;
  peak_vram_gb: number | null;
  total_vram_gb: number | null;
  avg_power_w: number | null;
  max_power_w: number | null;
  avg_fan_pct: number | null;
  max_fan_pct: number | null;
  avg_clock_mhz: number | null;
  max_clock_mhz: number | null;
  cool_pct: number;
  warm_pct: number;
  hot_pct: number;
  idle_pct: number;
  moderate_pct: number;
  heavy_pct: number;
  live?: {
    online: boolean;
    name: string;
    utilization: number | null;
    memory_used_mb: number | null;
    memory_total_mb: number | null;
    temperature_c: number | null;
    power_w: number | null;
    power_limit_w: number | null;
    fan_speed_pct: number | null;
    clock_sm_mhz: number | null;
    clock_mem_mhz: number | null;
    pstate: string | null;
  } | null;
}

export interface HardwareHostProfile {
  node_id: string;
  node_name: string;
  simulated: boolean;
  hours: number;
  samples: number;
  avg_cpu_pct: number | null;
  p95_cpu_pct: number | null;
  max_cpu_pct: number | null;
  avg_ram_pct: number | null;
  p95_ram_pct: number | null;
  max_ram_pct: number | null;
  avg_cpu_temp_c: number | null;
  max_cpu_temp_c: number | null;
  latest_disk_pct: number | null;
  avg_load_1m: number | null;
  runtimes_uptime_pct: Record<string, number | null>;
  live?: {
    online: boolean;
    maintenance: boolean;
    cpu_percent: number | null;
    cpu_temp_c: number | null;
    ram_percent: number | null;
    ram_used_gb: number | null;
    ram_total_gb: number | null;
    disk_percent: number | null;
    load_avg_1m: number | null;
  } | null;
}

export interface HardwareEventItem {
  id: string;
  recorded_at: string;
  node_id: string;
  node_name: string;
  gpu_index: number | null;
  severity: 'info' | 'warning' | 'critical';
  event_type: string;
  metric_name: string | null;
  metric_value: number | null;
  threshold: number | null;
  message: string;
  acknowledged: boolean;
  simulated: boolean;
}

export interface HardwareAnalytics {
  range: EnergyRange;
  bucket_seconds: number;
  generated_at: string;
  thresholds: {
    gpu_temp_warn_c: number;
    gpu_temp_crit_c: number;
    gpu_vram_warn_pct: number;
    gpu_vram_crit_pct: number;
  };
  summary: {
    health_score: number;
    sample_count: number;
    observed_hours: number;
    has_simulated: boolean;
    unacked_critical: number;
    unacked_warning: number;
    thermal: {
      mean_c: number | null;
      p50_c: number | null;
      p95_c: number | null;
      max_c: number | null;
      min_c: number | null;
      cool_pct: number;
      warm_pct: number;
      hot_pct: number;
      avg_fan_pct: number | null;
      avg_clock_mhz: number | null;
    };
    utilization: {
      mean_pct: number | null;
      p50_pct: number | null;
      p95_pct: number | null;
      max_pct: number | null;
      idle_pct: number;
      moderate_pct: number;
      heavy_pct: number;
    };
    vram: {
      mean_pct: number | null;
      p95_pct: number | null;
      max_pct: number | null;
      peak_gb: number | null;
      mean_gb: number | null;
    };
    host: {
      cpu_mean_pct: number | null;
      cpu_p95_pct: number | null;
      cpu_max_pct: number | null;
      ram_mean_pct: number | null;
      ram_p95_pct: number | null;
      ram_max_pct: number | null;
    };
    runtimes_uptime_pct: Record<string, number | null>;
  };
  gpu_series_meta: HardwareGpuSeriesMeta[];
  timeseries: HardwareTimeseriesPoint[];
  thermal_curve: HardwareThermalCurveBin[];
  gpu_profiles: HardwareGpuProfile[];
  host_profiles: HardwareHostProfile[];
  events: HardwareEventItem[];
  insights: EnergyInsight[];
  available_nodes: { id: string; name: string }[];
  available_gpu_indices: number[];
}
