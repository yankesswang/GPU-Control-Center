import React, { useCallback, useEffect, useState } from 'react';
import {
  Check,
  CircleNotch,
  DesktopTower,
  Fire,
  FloppyDisk,
  Lightning,
  Play,
  Plus,
  Queue,
  Scales,
  ShieldCheck,
  SlidersHorizontal,
  Sparkle,
  Thermometer,
  Trash,
  WarningCircle,
  X,
} from '@phosphor-icons/react';
import {
  api,
  type Model,
  type Node,
  type SchedulerDecisionItem,
  type SchedulerPolicy,
  type SchedulerPolicyMode,
  type SchedulerSimulationResult,
  type SchedulerState,
  type SlaClass,
} from '../api';
import { useI18n } from '../i18n';
import { Empty, Skeleton } from './ui';

const MODE_ORDER: SchedulerPolicyMode[] = [
  'balanced',
  'latency_first',
  'fair_share',
  'throughput_warm',
  'thermal_eco',
];

const SLA_OPTIONS: { value: SlaClass; zh: string; en: string }[] = [
  { value: 'realtime', zh: '即時 (realtime)', en: 'Realtime' },
  { value: 'interactive', zh: '互動 (interactive)', en: 'Interactive' },
  { value: 'standard', zh: '標準 (standard)', en: 'Standard' },
  { value: 'batch', zh: '批次 (batch)', en: 'Batch' },
  { value: 'background', zh: '背景 (background)', en: 'Background' },
];

function actionBadgeClass(action: string): string {
  if (action === 'dispatch' || action === 'dispatch_ready') return 'is-dispatch';
  if (action === 'yield') return 'is-yield';
  if (action === 'fallback' || action === 'fallback_ready' || action === 'fallback_dispatch') return 'is-fallback';
  if (action === 'defer' || action === 'deferred') return 'is-defer';
  if (action === 'reject' || action === 'reject_quota') return 'is-reject';
  return 'is-neutral';
}

function actionLabel(action: string, t: (zh: string, en: string) => string): string {
  switch (action) {
    case 'dispatch':
    case 'dispatch_ready':
      return t('派送執行', 'Dispatch');
    case 'yield':
      return t('智慧讓位', 'Yield Route');
    case 'fallback':
    case 'fallback_ready':
    case 'fallback_dispatch':
      return t('備援降級', 'Fallback');
    case 'defer':
    case 'deferred':
      return t('保護暫緩', 'Deferred');
    case 'reject':
    case 'reject_quota':
      return t('限流拒絕', 'Quota Reject');
    case 'waiting_slot':
    case 'queue_wait':
      return t('等待工作槽', 'Wait Slot');
    case 'no_compatible_node':
      return t('無相容主機', 'No Node');
    default:
      return action;
  }
}

export function SchedulerPanel({
  nodes,
  models,
  onOpenJob,
}: {
  nodes: Node[];
  models: Model[];
  onOpenJob: (id: string) => void;
}) {
  const { isZh, t, formatTime, blockerLabel, translateApiText } = useI18n();
  const [state, setState] = useState<SchedulerState | null>(null);
  const [draft, setDraft] = useState<SchedulerPolicy | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedToast, setSavedToast] = useState('');
  const [error, setError] = useState('');

  // Service weight quick-add state
  const [newSvcName, setNewSvcName] = useState('');
  const [newSvcWeight, setNewSvcWeight] = useState('1.5');

  // Decision log filter
  const [actionFilter, setActionFilter] = useState<string>('all');
  const [decisions, setDecisions] = useState<SchedulerDecisionItem[]>([]);

  // Simulator form state
  const llmModels = models.filter(m => !m.hidden && (!m.model_type || m.model_type === 'llm'));
  const [simModel, setSimModel] = useState(() => llmModels.find(m => m.available)?.id || llmModels[0]?.id || 'demo.progress');
  const [simSource, setSimSource] = useState('chat-api');
  const [simSla, setSimSla] = useState<SlaClass>('interactive');
  const [simPriority, setSimPriority] = useState('2');
  const [simTokens, setSimTokens] = useState('256');
  const [simDeadline, setSimDeadline] = useState('');
  const [simFallback, setSimFallback] = useState('');
  const [simBusy, setSimBusy] = useState(false);
  const [simResult, setSimResult] = useState<SchedulerSimulationResult | null>(null);

  useEffect(() => {
    if ((!simModel || !llmModels.some(m => m.id === simModel)) && llmModels.length > 0) {
      setSimModel(llmModels.find(m => m.available)?.id || llmModels[0].id);
    }
  }, [llmModels, simModel]);

  const load = useCallback(async () => {
    try {
      const qs = actionFilter === 'all' ? '?limit=40' : `?limit=40&action=${encodeURIComponent(actionFilter)}`;
      const [st, decs] = await Promise.all([
        api<SchedulerState>('/scheduler/state?decision_limit=30'),
        api<SchedulerDecisionItem[]>(`/scheduler/decisions${qs}`),
      ]);
      setState(st);
      setDecisions(decs);
      setDraft(prev => (prev && dirty ? prev : st.policy));
      setError('');
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('無法載入排程狀態', 'Failed to load scheduler state'));
    }
  }, [actionFilter, dirty, t, translateApiText]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 3000);
    return () => clearInterval(id);
  }, [load]);

  useEffect(() => {
    if (!savedToast) return;
    const id = setTimeout(() => setSavedToast(''), 2400);
    return () => clearTimeout(id);
  }, [savedToast]);

  async function savePolicy(nextPolicy: SchedulerPolicy, applyPreset = false) {
    setSaving(true);
    setError('');
    try {
      const payload = {
        mode: nextPolicy.mode,
        apply_preset: applyPreset,
        weight_priority: Number(nextPolicy.weight_priority),
        weight_aging: Number(nextPolicy.weight_aging),
        weight_deadline: Number(nextPolicy.weight_deadline),
        weight_fairness: Number(nextPolicy.weight_fairness),
        weight_warm_affinity: Number(nextPolicy.weight_warm_affinity),
        weight_node_health: Number(nextPolicy.weight_node_health),
        weight_sjf: Number(nextPolicy.weight_sjf),
        aging_interval_s: Number(nextPolicy.aging_interval_s),
        fairness_window_s: Number(nextPolicy.fairness_window_s),
        max_queued_per_source: Number(nextPolicy.max_queued_per_source),
        source_weights: nextPolicy.source_weights || {},
        thermal_soft_c: Number(nextPolicy.thermal_soft_c),
        thermal_hard_c: Number(nextPolicy.thermal_hard_c),
        vram_guard_pct: Number(nextPolicy.vram_guard_pct),
        comfyui_busy_penalty: Number(nextPolicy.comfyui_busy_penalty),
        defer_batch_on_peak_tariff: Boolean(nextPolicy.defer_batch_on_peak_tariff),
        route_reservation_s: Number(nextPolicy.route_reservation_s),
        routing_margin: Number(nextPolicy.routing_margin),
      };
      const updated = await api<SchedulerPolicy>('/scheduler/policy', 'PUT', payload);
      setDraft(updated);
      setDirty(false);
      setSavedToast(t('排程策略已即時生效', 'Scheduler policy applied live'));
      await load();
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('儲存策略失敗', 'Failed to update policy'));
    } finally {
      setSaving(false);
    }
  }

  function updateDraft(patch: Partial<SchedulerPolicy>) {
    setDraft(prev => (prev ? { ...prev, ...patch } : prev));
    setDirty(true);
  }

  function addSourceWeight() {
    const key = newSvcName.trim();
    const val = Number(newSvcWeight);
    if (!draft || !key || !Number.isFinite(val) || val < 0.1 || val > 10) return;
    updateDraft({ source_weights: { ...(draft.source_weights || {}), [key]: Number(val.toFixed(2)) } });
    setNewSvcName('');
    setNewSvcWeight('1.5');
  }

  function removeSourceWeight(key: string) {
    if (!draft) return;
    const next = { ...(draft.source_weights || {}) };
    delete next[key];
    updateDraft({ source_weights: next });
  }

  async function runSimulation(e: React.FormEvent) {
    e.preventDefault();
    if (!simModel) return;
    setSimBusy(true);
    setError('');
    try {
      const body: Record<string, unknown> = {
        model: simModel,
        source: simSource.trim() || 'simulator',
        sla_class: simSla,
        priority: Number(simPriority) || 0,
        max_tokens: Number(simTokens) || 256,
      };
      if (simDeadline.trim()) body.deadline_seconds = Number(simDeadline);
      if (simFallback.trim()) body.fallback_models = [simFallback.trim()];
      const res = await api<SchedulerSimulationResult>('/scheduler/simulate', 'POST', body);
      setSimResult(res);
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('模擬試算失敗', 'Simulation failed'));
    } finally {
      setSimBusy(false);
    }
  }

  if (!state || !draft) {
    return error ? (
      <div className="gcc-alert" role="alert">
        <WarningCircle size={20} />
        <p>{error}</p>
      </div>
    ) : (
      <Skeleton rows={5} />
    );
  }

  const presets = draft.presets || state.policy.presets;
  const activePresetMeta = presets?.[draft.mode];

  return (
    <section className="gcc-sched-workspace" aria-label={t('動態排程與路由控制中心', 'Dynamic Scheduler & Routing Center')}>
      {error && (
        <div className="gcc-alert" role="alert">
          <WarningCircle size={20} />
          <p>{error}</p>
          <button className="gcc-icon-button" aria-label={t('關閉錯誤', 'Dismiss')} onClick={() => setError('')}>
            <X size={18} />
          </button>
        </div>
      )}

      {savedToast && (
        <div className="gcc-sched-toast" role="status">
          <Check size={16} />
          <span>{savedToast}</span>
        </div>
      )}

      {/* Top KPI strip */}
      <div className="gcc-summary" aria-label={t('排程概況', 'Scheduler summary')}>
        <div className="gcc-stat gcc-stat--online is-highlighted">
          <span>
            <span className="gcc-stat-icon" aria-hidden="true"><SlidersHorizontal size={15} /></span>
            {t('排程模式', 'Active Mode')}
          </span>
          <div>
            <strong>{activePresetMeta ? (isZh ? activePresetMeta.label_zh.split('（')[0] : activePresetMeta.label_en.split(' (')[0]) : draft.mode}</strong>
            <span>{draft.mode}</span>
          </div>
        </div>
        <div className={`gcc-stat gcc-stat--running${state.cluster.ready_nodes > 0 ? ' is-highlighted' : ''}`}>
          <span>
            <span className="gcc-stat-icon" aria-hidden="true"><DesktopTower size={15} /></span>
            {t('可接單主機', 'Ready Hosts')}
          </span>
          <div>
            <strong>{state.cluster.ready_nodes}</strong>
            <span>/ {state.cluster.online_nodes} {t('在線', 'online')}</span>
          </div>
        </div>
        <div className={`gcc-stat gcc-stat--queued${state.cluster.queued_jobs > 0 ? ' is-highlighted' : ''}`}>
          <span>
            <span className="gcc-stat-icon" aria-hidden="true"><Queue size={15} /></span>
            {t('動態佇列', 'Queued Jobs')}
          </span>
          <div>
            <strong>{state.cluster.queued_jobs}</strong>
            <span>{state.cluster.active_services} {t('個來源服務', 'services')}</span>
          </div>
        </div>
        <div className="gcc-stat gcc-stat--completed">
          <span>
            <span className="gcc-stat-icon" aria-hidden="true"><Lightning size={15} /></span>
            {t('即時電價時段', 'Tariff Tier')}
          </span>
          <div>
            <strong>{state.cluster.tariff_tier === 'peak' ? t('尖峰', 'Peak') : state.cluster.tariff_tier === 'off_peak' ? t('離峰', 'Off-Peak') : t('單一', 'Flat')}</strong>
            <span>{state.cluster.current_rate.toFixed(2)} {state.cluster.currency}/kWh</span>
          </div>
        </div>
      </div>

      {/* Policy Presets & Tuning */}
      <article className="gcc-mcp-card">
        <div className="gcc-panel-head">
          <div>
            <h2><SlidersHorizontal size={18} /> {t('動態決策策略與權重', 'Dynamic Policy & Scoring Weights')}</h2>
            <p>{t('點擊預設策略立即切換，或展開細調多目標公式權重、服務公平權重與硬體保護護欄。', 'Switch presets instantly or tune multi-objective scoring weights, service weights, and thermal/VRAM guardrails.')}</p>
          </div>
          <div className="gcc-sched-actions">
            {dirty && (
              <button
                type="button"
                className="gcc-button gcc-button--quiet gcc-btn-sm"
                disabled={saving}
                onClick={() => { setDraft(state.policy); setDirty(false); }}
              >
                {t('還原', 'Reset')}
              </button>
            )}
            <button
              type="button"
              className="gcc-button gcc-button--primary gcc-btn-sm"
              disabled={saving || !dirty}
              onClick={() => void savePolicy(draft, false)}
            >
              {saving ? <CircleNotch size={15} className="gcc-spin" /> : <FloppyDisk size={15} />}
              {t('儲存自訂設定', 'Save Custom')}
            </button>
          </div>
        </div>

        <div className="gcc-sched-presets" role="radiogroup" aria-label={t('排程預設策略', 'Scheduler presets')}>
          {MODE_ORDER.map(modeKey => {
            const meta = presets?.[modeKey];
            const active = draft.mode === modeKey;
            return (
              <button
                key={modeKey}
                type="button"
                className={`gcc-sched-preset${active ? ' is-active' : ''}`}
                aria-pressed={active}
                disabled={saving}
                onClick={() => void savePolicy({ ...draft, mode: modeKey }, true)}
              >
                <strong>{meta ? (isZh ? meta.label_zh : meta.label_en) : modeKey}</strong>
                <span>{meta ? (isZh ? meta.description_zh : meta.description_en) : ''}</span>
              </button>
            );
          })}
        </div>

        <details className="gcc-disclosure">
          <summary><Scales size={17} /> {t('進階權重、護欄與服務公平權重設定', 'Advanced Weights, Guardrails & Per-Service Weights')}</summary>
          <div className="gcc-sched-tuning">
            <div className="gcc-sched-weight-grid">
              {[
                { key: 'weight_priority', zh: '優先級 & SLA 權重', en: 'Priority & SLA' },
                { key: 'weight_fairness', zh: '多服務公平權重', en: 'Fair-Share Weight' },
                { key: 'weight_warm_affinity', zh: '常駐模型熱命中權重', en: 'Warm Affinity' },
                { key: 'weight_node_health', zh: 'GPU 健康度權重', en: 'Node Health' },
                { key: 'weight_deadline', zh: '截止時間緊急度權重', en: 'Deadline Urgency' },
                { key: 'weight_aging', zh: '防餓死老化權重', en: 'Wait Aging' },
                { key: 'weight_sjf', zh: '短任務優先 (SJF)', en: 'Short-Job-First' },
              ].map(item => {
                const val = Number(draft[item.key as keyof SchedulerPolicy] ?? 1);
                return (
                  <label className="gcc-sched-slider" key={item.key}>
                    <div>
                      <span>{isZh ? item.zh : item.en}</span>
                      <code>{val.toFixed(1)}</code>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={6}
                      step={0.1}
                      value={val}
                      onChange={e => updateDraft({ [item.key]: Number(e.target.value) } as Partial<SchedulerPolicy>)}
                    />
                  </label>
                );
              })}
            </div>

            <div className="gcc-sched-guard-grid">
              <label className="gcc-field">
                <span>{t('單一服務最大排隊數（0=不限）', 'Max Queued per Source (0=∞)')}</span>
                <input
                  type="number"
                  min={0}
                  max={500}
                  value={draft.max_queued_per_source}
                  onChange={e => updateDraft({ max_queued_per_source: Math.max(0, Number(e.target.value) || 0) })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('公平統計時間窗（秒）', 'Fairness Window (s)')}</span>
                <input
                  type="number"
                  min={30}
                  max={3600}
                  value={draft.fairness_window_s}
                  onChange={e => updateDraft({ fairness_window_s: Math.max(30, Number(e.target.value) || 300) })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('多主機讓位窗口（秒）', 'Route Hold Window (s)')}</span>
                <input
                  type="number"
                  min={0}
                  max={60}
                  value={draft.route_reservation_s}
                  onChange={e => updateDraft({ route_reservation_s: Math.max(0, Number(e.target.value) || 0) })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('智慧讓位分差門檻', 'Routing Margin')}</span>
                <input
                  type="number"
                  min={0.1}
                  max={20}
                  step={0.1}
                  value={draft.routing_margin}
                  onChange={e => updateDraft({ routing_margin: Math.max(0.1, Number(e.target.value) || 1.5) })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('降分溫度軟門檻 (°C)', 'Thermal Soft Limit (°C)')}</span>
                <input
                  type="number"
                  min={50}
                  max={95}
                  value={draft.thermal_soft_c}
                  onChange={e => updateDraft({ thermal_soft_c: Number(e.target.value) || 80 })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('暫緩批次硬溫控 (°C)', 'Thermal Hard Guard (°C)')}</span>
                <input
                  type="number"
                  min={55}
                  max={100}
                  value={draft.thermal_hard_c}
                  onChange={e => updateDraft({ thermal_hard_c: Number(e.target.value) || 88 })}
                />
              </label>
              <label className="gcc-field">
                <span>{t('冷載入顯存警戒 (%)', 'VRAM Cold-Load Guard (%)')}</span>
                <input
                  type="number"
                  min={50}
                  max={100}
                  value={draft.vram_guard_pct}
                  onChange={e => updateDraft({ vram_guard_pct: Number(e.target.value) || 94 })}
                />
              </label>
              <label className="gcc-sched-checkbox">
                <input
                  type="checkbox"
                  checked={draft.defer_batch_on_peak_tariff}
                  onChange={e => updateDraft({ defer_batch_on_peak_tariff: e.target.checked })}
                />
                <span>{t('尖峰電價自動暫緩背景／批次任務', 'Defer background/batch jobs on peak tariff')}</span>
              </label>
            </div>

            <div className="gcc-sched-source-weights">
              <h3>{t('各服務公平權重加權（預設 1.0，越高配額越多）', 'Per-Service Fair-Share Weights (Default 1.0)')}</h3>
              <div className="gcc-sched-sw-add">
                <input
                  value={newSvcName}
                  onChange={e => setNewSvcName(e.target.value)}
                  placeholder={t('服務名稱 (source)，例如 chat-api', 'Service source tag, e.g. chat-api')}
                />
                <input
                  type="number"
                  min={0.1}
                  max={10}
                  step={0.1}
                  value={newSvcWeight}
                  onChange={e => setNewSvcWeight(e.target.value)}
                  style={{ width: 92 }}
                />
                <button type="button" className="gcc-button gcc-btn-sm" onClick={addSourceWeight}>
                  <Plus size={14} /> {t('加入權重', 'Add')}
                </button>
              </div>
              {Object.keys(draft.source_weights || {}).length > 0 && (
                <div className="gcc-sched-sw-pills">
                  {Object.entries(draft.source_weights).map(([src, w]) => (
                    <span className="gcc-sched-sw-pill" key={src}>
                      <code>{src}</code>
                      <b>×{w}</b>
                      <button type="button" aria-label={`Remove ${src}`} onClick={() => removeSourceWeight(src)}>
                        <Trash size={13} />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </details>
      </article>

      {/* Two-column live state: Host Health + Per-Service Fairness */}
      <div className="gcc-mcp-grid">
        <article className="gcc-mcp-card">
          <div className="gcc-panel-head">
            <div>
              <h2><DesktopTower size={18} /> {t('主機即時健康度與熱模型', 'Live Host Health & Warm Models')}</h2>
              <p>{t('排程演算法每輪即時評估的硬體與模型常駐狀態。', 'Live telemetry signals evaluated on every routing decision.')}</p>
            </div>
          </div>
          {state.nodes.length ? (
            <div className="gcc-sched-node-list">
              {state.nodes.map(n => (
                <div className={`gcc-sched-node-card${n.ready_slot ? ' is-ready' : ''}`} key={n.node_id}>
                  <div className="gcc-sched-node-top">
                    <div>
                      <strong>{n.name}</strong>
                      <small>{n.ready_slot ? t('可接單', 'Ready') : !n.online ? t('離線', 'Offline') : n.maintenance ? t('維護中', 'Maintenance') : t('工作槽佔用', 'Slot Occupied')}</small>
                    </div>
                    <span className={`gcc-grade-pill ${n.health_score >= 0.55 ? 'is-healthy' : n.health_score >= 0.2 ? 'is-watch' : 'is-hot'}`}>
                      {t('健康', 'Health')} {(n.health_score * 100).toFixed(0)}
                    </span>
                  </div>
                  <div className="gcc-host-summary-metrics">
                    <span>VRAM <b>{n.vram_used_pct != null ? `${n.vram_used_pct.toFixed(0)}%` : '—'}</b></span>
                    <span>Util <b>{n.avg_util_pct != null ? `${n.avg_util_pct.toFixed(0)}%` : '—'}</b></span>
                    <span><Thermometer size={12} /> <b>{n.max_temp_c != null ? `${n.max_temp_c.toFixed(0)}°C` : '—'}</b></span>
                    {n.comfyui_running > 0 && <span className="gcc-sched-warn"><Fire size={12} /> ComfyUI {n.comfyui_running}</span>}
                    {n.thermal_hard_blocked && <span className="gcc-sched-err">{t('硬溫控觸發', 'Thermal Guard')}</span>}
                  </div>
                  {n.warm_models.length > 0 && (
                    <div className="gcc-sched-warm-row">
                      <span className="gcc-sched-warm-label"><Sparkle size={12} /> {t('熱命中常駐', 'Warm')}:</span>
                      {n.warm_models.map(m => <code key={m}>{m.replace(/^(ollama|lmstudio):/, '')}</code>)}
                    </div>
                  )}
                  {n.active_holders.length > 0 && (
                    <div className="gcc-sched-holder">
                      {n.active_holders.map(h => (
                        <button type="button" key={h.id} className="gcc-job-link" onClick={() => onOpenJob(h.id)}>
                          {t('執行中', 'Active')}: {h.source} · {h.name}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <Empty icon={<DesktopTower size={24} />} title={t('尚無在線主機', 'No Registered Hosts')}>
              {t('連線本機或遠端 Agent 後會顯示即時健康評分。', 'Connect a host agent to view live routing health.')}
            </Empty>
          )}
        </article>

        <article className="gcc-mcp-card">
          <div className="gcc-panel-head">
            <div>
              <h2><Scales size={18} /> {t('多服務公平共享狀態', 'Multi-Service Fair-Share Monitor')}</h2>
              <p>{t(`過去 ${draft.fairness_window_s} 秒內各來源服務的算力佔用與公平壓力（壓力越低越優先）。`, `Rolling ${draft.fairness_window_s}s compute share per source (lower pressure gets priority).`)}</p>
            </div>
          </div>
          {state.services.length ? (
            <div className="gcc-desktop-table">
              <table className="gcc-bill-table">
                <thead>
                  <tr>
                    <th>{t('來源服務', 'Service')}</th>
                    <th>{t('權重', 'Weight')}</th>
                    <th>{t('執行 / 排隊', 'Active / Q')}</th>
                    <th>{t('近期耗時', 'Recent GPU')}</th>
                    <th>{t('公平壓力', 'Pressure')}</th>
                  </tr>
                </thead>
                <tbody>
                  {state.services.map(s => (
                    <tr key={s.source}>
                      <td><code>{s.source}</code></td>
                      <td>×{s.weight.toFixed(1)}</td>
                      <td>{s.active_jobs} / {s.queued_jobs}</td>
                      <td>{s.recent_compute_s.toFixed(0)}s</td>
                      <td>
                        <span className={`gcc-grade-pill ${s.fairness_pressure <= 0.35 ? 'is-healthy' : s.fairness_pressure <= 0.85 ? 'is-watch' : 'is-hot'}`}>
                          {s.fairness_pressure.toFixed(2)}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty icon={<Scales size={24} />} title={t('目前無活躍服務紀錄', 'No Recent Service Traffic')}>
              {t('當多個外部服務送出請求後，這裡會即時追蹤各服務的公平佔比。', 'Per-service fairness metrics appear here once requests arrive.')}
            </Empty>
          )}
        </article>
      </div>

      {/* Live Queue Dispatch Plan */}
      <article className="gcc-mcp-card">
        <div className="gcc-panel-head">
          <div>
            <h2><Queue size={18} /> {t('即時動態派送順序預覽', 'Live Dynamic Queue Dispatch Plan')}</h2>
            <p>{t('顯示目前佇列中每個任務的即時綜合評分、推薦主機與決策動作。', 'Live ranked order of queued jobs with recommended host, action, and score breakdown.')}</p>
          </div>
          <span className="gcc-subtle">{state.queue_plan.length} {t('筆排隊中', 'queued')}</span>
        </div>
        {state.queue_plan.length ? (
          <div className="gcc-desktop-table">
            <table className="gcc-bill-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>{t('任務 / 來源', 'Job / Source')}</th>
                  <th>{t('模型 / SLA', 'Model / SLA')}</th>
                  <th>{t('決策動作', 'Action')}</th>
                  <th>{t('推薦主機', 'Recommended Host')}</th>
                  <th>{t('動態評分拆解', 'Score Breakdown')}</th>
                </tr>
              </thead>
              <tbody>
                {state.queue_plan.map(item => (
                  <tr key={item.job_id}>
                    <td><strong>{item.rank}</strong></td>
                    <td>
                      <button type="button" className="gcc-job-link" onClick={() => onOpenJob(item.job_id)}>{item.name}</button>
                      <div><code className="gcc-muted">{item.source}</code> · <small>{item.wait_seconds}s</small></div>
                    </td>
                    <td>
                      <div><code>{item.effective_model.replace(/^(ollama|lmstudio):/, '')}</code>{item.used_fallback && <span className="gcc-sched-pill is-fallback">FB</span>}</div>
                      <span className="gcc-sched-sla">{item.sla_class} · P{item.priority}</span>
                    </td>
                    <td>
                      <span className={`gcc-sched-pill ${actionBadgeClass(item.decision_action)}`}>{actionLabel(item.decision_action, t)}</span>
                    </td>
                    <td>
                      {item.recommended_node_name ? (
                        <div>
                          <strong>{item.recommended_node_name}</strong>
                          {item.warm_hit && <span className="gcc-sched-pill is-dispatch">{t('熱命中', 'Warm')}</span>}
                        </div>
                      ) : (
                        <span className="gcc-muted">{item.blockers.map(b => blockerLabel(b)).join(', ')}</span>
                      )}
                    </td>
                    <td>
                      <strong className="gcc-sched-score">{item.score.toFixed(2)}</strong>
                      <div className="gcc-sched-breakdown">
                        {Object.entries(item.breakdown)
                          .filter(([k, v]) => k !== 'total' && Math.abs(Number(v)) >= 0.05)
                          .slice(0, 5)
                          .map(([k, v]) => (
                            <span key={k}>{k}: {Number(v) >= 0 ? `+${Number(v).toFixed(1)}` : Number(v).toFixed(1)}</span>
                          ))}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty icon={<ShieldCheck size={24} />} title={t('佇列清空', 'Queue Empty')}>
            {t('目前沒有排隊等待分配的任務；可用下方模擬器試算任意請求的路由結果。', 'No queued jobs waiting. Use the simulator below to test routing behavior.')}
          </Empty>
        )}
      </article>

      {/* What-If Simulator */}
      <article className="gcc-mcp-card">
        <div className="gcc-panel-head">
          <div>
            <h2><Play size={18} /> {t('動態決策試算器（What-If Simulator）', 'Dynamic Dispatch Simulator (Dry Run)')}</h2>
            <p>{t('在不實際建立任務的前提下，測試某個外部服務此刻送出推論請求時，演算法會怎麼決定。', 'Preview which host and action the algorithm selects right now without queueing a real job.')}</p>
          </div>
        </div>
        <form className="gcc-sched-sim-form" onSubmit={e => void runSimulation(e)}>
          <label className="gcc-field">
            <span>{t('模型', 'Model')}</span>
            <select value={simModel} onChange={e => setSimModel(e.target.value)}>
              {llmModels.map(m => (
                <option key={m.id} value={m.id}>{m.display_name || m.id}</option>
              ))}
              {llmModels.length === 0 && <option value="demo.progress">demo.progress</option>}
            </select>
          </label>
          <label className="gcc-field">
            <span>{t('來源服務', 'Service Source')}</span>
            <input value={simSource} onChange={e => setSimSource(e.target.value)} maxLength={80} />
          </label>
          <label className="gcc-field">
            <span>{t('SLA 等級', 'SLA Class')}</span>
            <select value={simSla} onChange={e => setSimSla(e.target.value as SlaClass)}>
              {SLA_OPTIONS.map(o => <option key={o.value} value={o.value}>{isZh ? o.zh : o.en}</option>)}
            </select>
          </label>
          <label className="gcc-field">
            <span>{t('優先級 (0-10)', 'Priority')}</span>
            <input type="number" min={0} max={10} value={simPriority} onChange={e => setSimPriority(e.target.value)} />
          </label>
          <label className="gcc-field">
            <span>{t('預估 Tokens', 'Max Tokens')}</span>
            <input type="number" min={1} max={2048} value={simTokens} onChange={e => setSimTokens(e.target.value)} />
          </label>
          <label className="gcc-field">
            <span>{t('截止秒數（選填）', 'Deadline s (opt)')}</span>
            <input type="number" min={5} max={86400} placeholder="—" value={simDeadline} onChange={e => setSimDeadline(e.target.value)} />
          </label>
          <label className="gcc-field">
            <span>{t('備援模型（選填）', 'Fallback Model')}</span>
            <select value={simFallback} onChange={e => setSimFallback(e.target.value)}>
              <option value="">{t('不使用備援', 'None')}</option>
              {llmModels.filter(m => m.id !== simModel).map(m => (
                <option key={m.id} value={m.id}>{m.display_name || m.id}</option>
              ))}
            </select>
          </label>
          <div className="gcc-sched-sim-submit">
            <button type="submit" className="gcc-button gcc-button--primary" disabled={simBusy || !simModel}>
              {simBusy ? <CircleNotch size={16} className="gcc-spin" /> : <Play size={16} />}
              {t('試算決策', 'Simulate')}
            </button>
          </div>
        </form>

        {simResult && (
          <div className="gcc-sched-sim-out">
            <div className="gcc-sched-sim-banner">
              <span className={`gcc-sched-pill ${actionBadgeClass(simResult.decision_action)}`}>{actionLabel(simResult.decision_action, t)}</span>
              <strong>{simResult.summary}</strong>
              {simResult.selected_score !== null && <code>Score {simResult.selected_score.toFixed(2)}</code>}
            </div>
            {simResult.candidates.length > 0 && (
              <div className="gcc-desktop-table">
                <table className="gcc-bill-table">
                  <thead>
                    <tr>
                      <th>{t('候選主機', 'Candidate Host')}</th>
                      <th>{t('模型', 'Model')}</th>
                      <th>{t('狀態 / 阻擋', 'Status / Blockers')}</th>
                      <th>{t('健康度 / 溫度 / VRAM', 'Health / Temp / VRAM')}</th>
                      <th>{t('總評分與細項', 'Score & Breakdown')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {simResult.candidates.map(c => (
                      <tr key={c.node_id} className={c.recommended ? 'is-recommended-row' : undefined}>
                        <td>
                          <strong>{c.node_name}</strong>
                          {c.recommended && <span className="gcc-sched-pill is-dispatch">{t('勝出', 'Winner')}</span>}
                          {c.warm_hit && <span className="gcc-sched-pill is-yield">{t('熱命中', 'Warm')}</span>}
                        </td>
                        <td><code>{c.effective_model.replace(/^(ollama|lmstudio):/, '')}</code></td>
                        <td>{c.runnable ? t('可立即執行', 'Runnable') : c.blockers.map(b => blockerLabel(b)).join(', ')}</td>
                        <td>
                          {(c.health_score * 100).toFixed(0)} · {c.max_temp_c != null ? `${c.max_temp_c}°C` : '—'} · {c.vram_used_pct != null ? `${c.vram_used_pct}%` : '—'}
                        </td>
                        <td>
                          <strong className="gcc-sched-score">{c.score.toFixed(2)}</strong>
                          <div className="gcc-sched-breakdown">
                            {Object.entries(c.breakdown)
                              .filter(([k, v]) => k !== 'total' && Math.abs(Number(v)) >= 0.05)
                              .map(([k, v]) => (
                                <span key={k}>{k}: {Number(v) >= 0 ? `+${Number(v).toFixed(1)}` : Number(v).toFixed(1)}</span>
                              ))}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </article>

      {/* Decision Audit Log */}
      <article className="gcc-mcp-card">
        <div className="gcc-panel-head">
          <div>
            <h2><ShieldCheck size={18} /> {t('動態決策追蹤日誌', 'Scheduler Decision Audit Log')}</h2>
            <p>{t('完整紀錄每次派送、智慧讓位、保護暫緩、備援降級與限流拒絕的依據。', 'Audit trail of every dispatch, route yield, thermal/tariff defer, fallback, and quota rejection.')}</p>
          </div>
          <div className="gcc-mcp-role-filter" role="group" aria-label={t('篩選決策類型', 'Filter decision actions')}>
            {['all', 'dispatch', 'yield', 'fallback', 'defer', 'reject'].map(act => (
              <button
                key={act}
                type="button"
                className={`gcc-button gcc-button--quiet gcc-btn-sm${actionFilter === act ? ' is-selected' : ''}`}
                onClick={() => setActionFilter(act)}
              >
                {act === 'all' ? t('全部', 'All') : actionLabel(act, t)}
              </button>
            ))}
          </div>
        </div>
        {decisions.length ? (
          <div className="gcc-desktop-table">
            <table className="gcc-bill-table">
              <thead>
                <tr>
                  <th>{t('時間', 'Time')}</th>
                  <th>{t('動作', 'Action')}</th>
                  <th>{t('來源 / 任務', 'Source / Job')}</th>
                  <th>{t('目標主機 / 分數', 'Host / Score')}</th>
                  <th>{t('決策原因', 'Reason')}</th>
                </tr>
              </thead>
              <tbody>
                {decisions.map(d => (
                  <tr key={d.id}>
                    <td><time dateTime={d.recorded_at}>{formatTime(d.recorded_at)}</time></td>
                    <td><span className={`gcc-sched-pill ${actionBadgeClass(d.action)}`}>{actionLabel(d.action, t)}</span></td>
                    <td>
                      <div><code>{d.source || '—'}</code>{d.sla_class && <small className="gcc-muted"> · {d.sla_class}</small>}</div>
                      {d.job_id ? (
                        <button type="button" className="gcc-job-link" onClick={() => onOpenJob(d.job_id!)}>
                          {d.job_name || d.job_id.slice(0, 8)}
                        </button>
                      ) : (
                        <span className="gcc-muted">{d.model || '—'}</span>
                      )}
                    </td>
                    <td>
                      <strong>{d.selected_node_name || '—'}</strong>
                      {d.score != null && <div className="gcc-muted">Score {d.score.toFixed(2)}</div>}
                    </td>
                    <td>{d.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <Empty icon={<SlidersHorizontal size={24} />} title={t('尚無決策紀錄', 'No Decisions Yet')}>
            {t('任務提交與派送後，這裡會自動紀錄每次演算法決策。', 'Decisions will appear here automatically as jobs are submitted and routed.')}
          </Empty>
        )}
      </article>
      {/* Keep nodes prop referenced cleanly */}
      {nodes.length < 0 && null}
    </section>
  );
}
