import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Bell,
  Check,
  CheckCircle,
  CircleNotch,
  Cpu,
  DesktopTower,
  DownloadSimple,
  Fan,
  Fire,
  Gauge,
  HardDrives,
  Heartbeat,
  Info,
  Memory,
  Plus,
  Pulse,
  ShieldCheck,
  Sparkle,
  Thermometer,
  Trash,
  WarningCircle,
  X,
} from '@phosphor-icons/react';
import {
  api,
  type EnergyRange,
  type HardwareAnalytics,
  type Node,
} from '../api';
import { useI18n } from '../i18n';
import {
  HardwareTelemetryChart,
  ThermalLoadCurveChart,
  type TelemetryChartMode,
} from './HardwareHistoryCharts';
import { Empty, Skeleton } from './ui';

const RANGES: { id: EnergyRange; zh: string; en: string }[] = [
  { id: '1h', zh: '1小時', en: '1H' },
  { id: '6h', zh: '6小時', en: '6H' },
  { id: '24h', zh: '24小時', en: '24H' },
  { id: '7d', zh: '7天', en: '7D' },
  { id: '30d', zh: '30天', en: '30D' },
  { id: '90d', zh: '90天', en: '90D' },
  { id: 'all', zh: '全部', en: 'ALL' },
];

export function HardwareHistoryPanel({ nodes }: { nodes: Node[] }) {
  const { t, isZh, formatTime } = useI18n();
  const [range, setRange] = useState<EnergyRange>('24h');
  const [nodeFilter, setNodeFilter] = useState('');
  const [gpuFilter, setGpuFilter] = useState('');
  const [includeSimulated, setIncludeSimulated] = useState(false);
  const [chartMode, setChartMode] = useState<TelemetryChartMode>('thermal');
  const [sevFilter, setSevFilter] = useState<'all' | 'critical' | 'warning' | 'info'>('all');
  const [analytics, setAnalytics] = useState<HardwareAnalytics | null>(null);
  const [error, setError] = useState('');
  const [addingNote, setAddingNote] = useState(false);
  const [busyEventId, setBusyEventId] = useState<string | null>(null);
  const loadingRef = useRef(false);

  const hasDemoNodes = nodes.some(n => n.mode === 'demo' || n.metrics?.simulated);

  const loadAnalytics = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      const qs = new URLSearchParams({ range });
      if (nodeFilter) qs.set('node_id', nodeFilter);
      if (gpuFilter !== '') qs.set('gpu_index', gpuFilter);
      if (includeSimulated) qs.set('include_simulated', 'true');
      const res = await api<HardwareAnalytics>(`/hardware/analytics?${qs.toString()}`);
      setAnalytics(res);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('無法載入硬體追蹤資料', 'Failed to load hardware analytics'));
    } finally {
      loadingRef.current = false;
    }
  }, [range, nodeFilter, gpuFilter, includeSimulated, t]);

  useEffect(() => {
    void loadAnalytics();
    const id = setInterval(() => void loadAnalytics(), 5000);
    return () => clearInterval(id);
  }, [loadAnalytics]);

  const exportUrl = (target: 'timeseries' | 'gpus' | 'events') => {
    const qs = new URLSearchParams({ target, range });
    if (nodeFilter) qs.set('node_id', nodeFilter);
    if (gpuFilter !== '') qs.set('gpu_index', gpuFilter);
    if (includeSimulated) qs.set('include_simulated', 'true');
    return `/api/hardware/export?${qs.toString()}`;
  };

  const toggleAck = async (eventId: string, acknowledged: boolean) => {
    setBusyEventId(eventId);
    try {
      await api(`/hardware/events/${eventId}/ack`, 'PATCH', { acknowledged });
      await loadAnalytics();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('更新失敗', 'Update failed'));
    } finally {
      setBusyEventId(null);
    }
  };

  const ackAll = async () => {
    setBusyEventId('__all__');
    try {
      const qs = nodeFilter ? `?node_id=${encodeURIComponent(nodeFilter)}` : '';
      await api(`/hardware/events/ack-all${qs}`, 'POST');
      await loadAnalytics();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('操作失敗', 'Action failed'));
    } finally {
      setBusyEventId(null);
    }
  };

  const deleteEvent = async (eventId: string) => {
    setBusyEventId(eventId);
    try {
      await api(`/hardware/events/${eventId}`, 'DELETE');
      await loadAnalytics();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('刪除失敗', 'Delete failed'));
    } finally {
      setBusyEventId(null);
    }
  };

  const eventTypeLabel = (et: string) => {
    switch (et) {
      case 'high_temp': return t('高溫警戒', 'High Temp');
      case 'vram_pressure': return t('顯存壓力', 'VRAM Pressure');
      case 'high_power': return t('功耗觸頂', 'Power Cap');
      case 'cpu_pressure': return t('CPU 高載', 'CPU Pressure');
      case 'ram_pressure': return t('RAM 壓力', 'RAM Pressure');
      case 'node_recovered': return t('主機復線', 'Host Recovered');
      case 'maintenance_on': return t('進入維護', 'Maintenance On');
      case 'maintenance_off': return t('解除維護', 'Maintenance Off');
      case 'manual_note': return t('維運備註', 'Ops Note');
      default: return et;
    }
  };

  const gradeLabel = (g: 'healthy' | 'watch' | 'hot') => {
    if (g === 'hot') return t('高溫／壓力警戒', 'Thermal / VRAM Alert');
    if (g === 'watch') return t('留意觀察', 'Watch');
    return t('健康穩定', 'Healthy');
  };

  if (!analytics && !error) {
    return <Skeleton rows={6} />;
  }

  const sum = analytics?.summary;
  const filteredEvents = (analytics?.events || []).filter(e => sevFilter === 'all' || e.severity === sevFilter);
  const unackedTotal = (sum?.unacked_critical || 0) + (sum?.unacked_warning || 0);

  return (
    <section className="gcc-energy" aria-label={t('硬體歷史狀態追蹤與後台診斷', 'Hardware Status History and Diagnostics')}>
      {/* Control Toolbar */}
      <div className="gcc-energy-toolbar">
        <div className="gcc-energy-toolbar-left">
          <div className="gcc-range-group" role="group" aria-label={t('追蹤時間區間', 'Tracking time range')}>
            {RANGES.map(r => (
              <button
                key={r.id}
                type="button"
                className={`gcc-range-btn${range === r.id ? ' is-active' : ''}`}
                aria-pressed={range === r.id}
                onClick={() => setRange(r.id)}
              >
                {isZh ? r.zh : r.en}
              </button>
            ))}
          </div>
          <label className="gcc-energy-node-select">
            <span className="sr-only">{t('篩選主機', 'Filter host')}</span>
            <select value={nodeFilter} onChange={e => setNodeFilter(e.target.value)}>
              <option value="">{t('全部主機', 'All Hosts')}</option>
              {(analytics?.available_nodes || []).map(n => (
                <option key={n.id} value={n.id}>{n.name}</option>
              ))}
            </select>
          </label>
          <label className="gcc-energy-node-select">
            <span className="sr-only">{t('篩選顯示卡', 'Filter GPU')}</span>
            <select value={gpuFilter} onChange={e => setGpuFilter(e.target.value)}>
              <option value="">{t('全部 GPU', 'All GPUs')}</option>
              {(analytics?.available_gpu_indices || []).map(idx => (
                <option key={idx} value={String(idx)}>GPU {String(idx).padStart(2, '0')}</option>
              ))}
            </select>
          </label>
          {hasDemoNodes && (
            <label className="gcc-energy-sim-toggle">
              <input
                type="checkbox"
                checked={includeSimulated}
                onChange={e => setIncludeSimulated(e.target.checked)}
              />
              <span>{t('含模擬主機', 'Include simulated')}</span>
            </label>
          )}
        </div>
        <div className="gcc-energy-toolbar-right">
          <a className="gcc-button" href={exportUrl('timeseries')} download>
            <DownloadSimple size={16} />
            {t('匯出遙測 CSV', 'Export CSV')}
          </a>
          <button
            type="button"
            className="gcc-button gcc-button--primary"
            onClick={() => setAddingNote(true)}
          >
            <Plus size={16} />
            {t('新增維運紀錄', 'Add Ops Log')}
          </button>
        </div>
      </div>

      {error && (
        <div className="gcc-alert" role="alert">
          <WarningCircle size={19} />
          <p>{error}</p>
          <button type="button" className="gcc-button gcc-button--quiet" onClick={() => void loadAnalytics()}>
            {t('重試', 'Retry')}
          </button>
        </div>
      )}

      {analytics && sum && (
        <>
          {sum.has_simulated && (
            <div className="gcc-energy-sim-banner">
              <Info size={16} />
              <span>{t('目前包含模擬主機（Simulated）的遙測紀錄；正式硬體健康追蹤建議關閉「含模擬主機」。', 'Simulated demo telemetry is included. Uncheck "Include simulated" for real hardware tracking.')}</span>
            </div>
          )}

          {/* 6-Card Fleet Health KPI Strip */}
          <div className="gcc-energy-kpis">
            <article className={`gcc-kpi-card ${sum.health_score >= 85 ? 'is-accent' : sum.health_score >= 65 ? 'is-warn' : 'is-error'}`}>
              <div className="gcc-kpi-head">
                <span><ShieldCheck size={15} />{t('硬體健康評分', 'Fleet Health Score')}</span>
                <span className={`gcc-tier-pill ${unackedTotal > 0 ? 'is-peak' : 'is-off_peak'}`}>
                  {unackedTotal > 0 ? `${unackedTotal} ${t('待確認', 'open')}` : t('正常', 'Nominal')}
                </span>
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.health_score}</strong>
                <span>/ 100</span>
              </div>
              <p className="gcc-kpi-sub">
                {t('危急', 'Crit')} {sum.unacked_critical} · {t('警戒', 'Warn')} {sum.unacked_warning} · {sum.sample_count.toLocaleString()} {t('筆紀錄', 'samples')}
              </p>
            </article>

            <article className="gcc-kpi-card is-warn">
              <div className="gcc-kpi-head">
                <span><Thermometer size={15} />{t('GPU 溫度統計', 'GPU Thermal Profile')}</span>
                {sum.thermal.max_c != null && <code>{t('峰', 'Max')} {sum.thermal.max_c.toFixed(0)}°C</code>}
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.thermal.mean_c != null ? sum.thermal.mean_c.toFixed(1) : '—'}</strong>
                <span>°C {t('均溫', 'avg')}</span>
              </div>
              <p className="gcc-kpi-sub">
                P95 {sum.thermal.p95_c != null ? `${sum.thermal.p95_c.toFixed(0)}°C` : '—'}
                {sum.thermal.avg_fan_pct != null && ` · ${t('風扇', 'Fan')} ${sum.thermal.avg_fan_pct.toFixed(0)}%`}
                {sum.thermal.avg_clock_mhz != null && ` · ${Math.round(sum.thermal.avg_clock_mhz)} MHz`}
              </p>
            </article>

            <article className="gcc-kpi-card is-accent">
              <div className="gcc-kpi-head">
                <span><Gauge size={15} />{t('GPU 算力利用率', 'GPU Compute Util')}</span>
                {sum.utilization.p95_pct != null && <code>P95 {sum.utilization.p95_pct.toFixed(0)}%</code>}
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.utilization.mean_pct != null ? sum.utilization.mean_pct.toFixed(1) : '—'}</strong>
                <span>%</span>
              </div>
              <p className="gcc-kpi-sub">
                {t('高載≥70%', 'Heavy')} {sum.utilization.heavy_pct.toFixed(0)}% · {t('待機', 'Idle')} {sum.utilization.idle_pct.toFixed(0)}%
              </p>
            </article>

            <article className="gcc-kpi-card is-info">
              <div className="gcc-kpi-head">
                <span><Memory size={15} />{t('VRAM 顯存壓力', 'VRAM Pressure')}</span>
                {sum.vram.max_pct != null && (
                  <code className={sum.vram.max_pct >= 88 ? 'is-over' : ''}>
                    {t('峰', 'Peak')} {sum.vram.max_pct.toFixed(0)}%
                  </code>
                )}
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.vram.mean_pct != null ? sum.vram.mean_pct.toFixed(1) : '—'}</strong>
                <span>%</span>
              </div>
              <p className="gcc-kpi-sub">
                {t('均值', 'Mean')} {sum.vram.mean_gb != null ? `${sum.vram.mean_gb.toFixed(1)} GiB` : '—'} · {t('峰值', 'Peak')} {sum.vram.peak_gb != null ? `${sum.vram.peak_gb.toFixed(1)} GiB` : '—'}
              </p>
            </article>

            <article className="gcc-kpi-card is-purple">
              <div className="gcc-kpi-head">
                <span><Cpu size={15} />{t('主機 CPU / RAM', 'Host CPU & RAM')}</span>
                {sum.host.cpu_p95_pct != null && <code>CPU P95 {sum.host.cpu_p95_pct.toFixed(0)}%</code>}
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.host.ram_mean_pct != null ? sum.host.ram_mean_pct.toFixed(0) : '—'}</strong>
                <span>% RAM</span>
              </div>
              <p className="gcc-kpi-sub">
                CPU {t('均', 'avg')} {sum.host.cpu_mean_pct != null ? `${sum.host.cpu_mean_pct.toFixed(1)}%` : '—'} · RAM {t('峰', 'max')} {sum.host.ram_max_pct != null ? `${sum.host.ram_max_pct.toFixed(0)}%` : '—'}
              </p>
            </article>

            <article className="gcc-kpi-card">
              <div className="gcc-kpi-head">
                <span><Heartbeat size={15} />{t('監控時數與執行器 SLA', 'Observed & Runtime SLA')}</span>
                <code>{sum.observed_hours.toFixed(2)}h</code>
              </div>
              <div className="gcc-kpi-val">
                <strong>{analytics.gpu_profiles.length}</strong>
                <span>{t('張 GPU 追蹤中', 'GPUs tracked')}</span>
              </div>
              <p className="gcc-kpi-sub">
                LM {sum.runtimes_uptime_pct.lmstudio != null ? `${sum.runtimes_uptime_pct.lmstudio.toFixed(0)}%` : '—'} ·
                Oll {sum.runtimes_uptime_pct.ollama != null ? `${sum.runtimes_uptime_pct.ollama.toFixed(0)}%` : '—'} ·
                Cfy {sum.runtimes_uptime_pct.comfyui != null ? `${sum.runtimes_uptime_pct.comfyui.toFixed(0)}%` : '—'}
              </p>
            </article>
          </div>

          {/* Primary Multi-Metric Telemetry Chart */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('硬體狀態歷史時間軸', 'Hardware Telemetry Timeline')}</h2>
                <p>
                  {t(
                    '切換檢視各顯示卡核心溫度與風扇轉速、算力與顯存佔用率，或主機 CPU／記憶體走勢。',
                    'Inspect multi-GPU temperature & fan trends, compute & VRAM utilization, or host CPU/RAM history.',
                  )}
                </p>
              </div>
              <div className="gcc-chart-mode-switch" role="group" aria-label={t('切換監控指標', 'Switch telemetry view')}>
                <button type="button" className={chartMode === 'thermal' ? 'is-active' : ''} onClick={() => setChartMode('thermal')}>
                  <Thermometer size={15} />
                  {t('溫度 / 風扇', 'Thermal')}
                </button>
                <button type="button" className={chartMode === 'compute' ? 'is-active' : ''} onClick={() => setChartMode('compute')}>
                  <Pulse size={15} />
                  {t('算力 / VRAM', 'Util & VRAM')}
                </button>
                <button type="button" className={chartMode === 'system' ? 'is-active' : ''} onClick={() => setChartMode('system')}>
                  <DesktopTower size={15} />
                  {t('CPU / RAM', 'Host CPU/RAM')}
                </button>
              </div>
            </div>
            <div className="gcc-energy-card-body">
              <HardwareTelemetryChart
                points={analytics.timeseries}
                gpuMeta={analytics.gpu_series_meta}
                mode={chartMode}
                tempWarnC={analytics.thresholds.gpu_temp_warn_c}
                tempCritC={analytics.thresholds.gpu_temp_crit_c}
              />
            </div>
          </section>

          {/* 2-Column Deep Diagnostics: Thermal Load Curve + Band Breakdown & Automated Insights */}
          <div className="gcc-energy-two-col">
            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('散熱回應曲線（負載 % vs 溫度 & 風扇）', 'Thermal Response Curve (Load % vs Temp & Fan)')}</h2>
                  <p>{t('將 GPU 負載分為 10 個級距，追蹤高負載時的升溫斜率與風扇介入表現。', 'Bins GPU utilization into 10% buckets to evaluate cooling slope and fan response under load.')}</p>
                </div>
              </div>
              <div className="gcc-energy-card-body">
                <ThermalLoadCurveChart bins={analytics.thermal_curve} />

                {/* Thermal & Utilization Band Stacked Distribution */}
                <div className="gcc-hw-bands">
                  <div className="gcc-hw-band-row">
                    <div className="gcc-hw-band-head">
                      <span>{t('溫度區間分布', 'Thermal Zone Distribution')}</span>
                      <small>
                        {t('涼爽 <60°C', 'Cool')} {sum.thermal.cool_pct}% · {t('溫熱 60–75°C', 'Warm')} {sum.thermal.warm_pct}% · {t('高溫 ≥75°C', 'Hot')} {sum.thermal.hot_pct}%
                      </small>
                    </div>
                    <div className="gcc-stacked-bar">
                      <span className="is-cool" style={{ width: `${sum.thermal.cool_pct}%` }} />
                      <span className="is-warm" style={{ width: `${sum.thermal.warm_pct}%` }} />
                      <span className="is-hot" style={{ width: `${sum.thermal.hot_pct}%` }} />
                    </div>
                  </div>
                  <div className="gcc-hw-band-row">
                    <div className="gcc-hw-band-head">
                      <span>{t('算力負載分布', 'Compute Load Distribution')}</span>
                      <small>
                        {t('待機 <10%', 'Idle')} {sum.utilization.idle_pct}% · {t('中載', 'Mid')} {sum.utilization.moderate_pct}% · {t('高載 ≥70%', 'Heavy')} {sum.utilization.heavy_pct}%
                      </small>
                    </div>
                    <div className="gcc-stacked-bar">
                      <span className="is-idle" style={{ width: `${sum.utilization.idle_pct}%` }} />
                      <span className="is-info" style={{ width: `${sum.utilization.moderate_pct}%` }} />
                      <span className="is-cool" style={{ width: `${sum.utilization.heavy_pct}%` }} />
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('後台診斷洞察與主機摘要', 'Backend Diagnostics & Host Summary')}</h2>
                  <p>{t('自動偵測溫控風險、顯存瓶頸、多卡負載不均與系統資源狀態。', 'Automated checks for thermal risk, VRAM saturation, GPU imbalance, and host bottlenecks.')}</p>
                </div>
              </div>
              <div className="gcc-energy-card-body">
                <div className="gcc-insights-list">
                  {analytics.insights.map(ins => (
                    <article key={ins.id} className={`gcc-insight-item is-${ins.severity}`}>
                      <div className="gcc-insight-icon">
                        {ins.severity === 'positive' ? <CheckCircle size={17} /> : ins.severity === 'warning' ? <Fire size={17} /> : <Sparkle size={17} />}
                      </div>
                      <div className="gcc-insight-content">
                        <div className="gcc-insight-top">
                          <strong>{isZh ? ins.title_zh : ins.title_en}</strong>
                          <code>{ins.metric}</code>
                        </div>
                        <p>{isZh ? ins.body_zh : ins.body_en}</p>
                      </div>
                    </article>
                  ))}
                </div>

                {analytics.host_profiles.length > 0 && (
                  <div className="gcc-host-summary-list">
                    {analytics.host_profiles.map(h => (
                      <div key={h.node_id} className="gcc-host-summary-card">
                        <div className="gcc-host-summary-top">
                          <strong><DesktopTower size={15} /> {h.node_name}</strong>
                          <code>{h.hours.toFixed(2)}h · {h.samples.toLocaleString()} {t('樣本', 'samples')}</code>
                        </div>
                        <div className="gcc-host-summary-metrics">
                          <span>CPU {t('均/峰', 'avg/max')} <b>{h.avg_cpu_pct ?? '—'}% / {h.max_cpu_pct ?? '—'}%</b></span>
                          <span>RAM {t('均/峰', 'avg/max')} <b>{h.avg_ram_pct ?? '—'}% / {h.max_ram_pct ?? '—'}%</b></span>
                          {h.avg_cpu_temp_c != null && <span>CPU Temp <b>{h.avg_cpu_temp_c}°C</b></span>}
                          {h.latest_disk_pct != null && <span><HardDrives size={12} /> Disk <b>{h.latest_disk_pct}%</b></span>}
                          {h.avg_load_1m != null && <span>Load1m <b>{h.avg_load_1m}</b></span>}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </section>
          </div>

          {/* Per-GPU Engineering Scorecard Grid */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('各顯示卡後台健康診斷卡', 'Per-GPU Engineering Diagnostic Scorecards')}</h2>
                <p>{t('彙整每張顯示卡的即時時脈／風扇／P-State 與歷史統計分位數（均／P95／峰值）。', 'Live clocks, fan speed, and P-State paired with historical mean, P95, and peak quantiles per GPU.')}</p>
              </div>
              <a className="gcc-button gcc-button--quiet gcc-btn-sm" href={exportUrl('gpus')} download>
                <DownloadSimple size={15} />
                {t('匯出 GPU CSV', 'GPUs CSV')}
              </a>
            </div>
            <div className="gcc-energy-card-body">
              {analytics.gpu_profiles.length > 0 ? (
                <div className="gcc-gpu-scorecards">
                  {analytics.gpu_profiles.map(g => (
                    <article key={`${g.node_id}-${g.gpu_index}`} className={`gcc-gpu-scorecard is-${g.health_grade}`}>
                      <header className="gcc-gpu-scorecard-head">
                        <div>
                          <span className="gcc-gpu-scorecard-sub">{g.node_name} · GPU {String(g.gpu_index).padStart(2, '0')}</span>
                          <h3>{g.gpu_name}</h3>
                        </div>
                        <span className={`gcc-grade-pill is-${g.health_grade}`}>{gradeLabel(g.health_grade)}</span>
                      </header>

                      {g.live && (
                        <div className="gcc-gpu-live-strip">
                          <span><Thermometer size={13} /> {g.live.temperature_c != null ? `${g.live.temperature_c.toFixed(0)}°C` : '—'}</span>
                          <span><Fan size={13} /> {g.live.fan_speed_pct != null ? `${g.live.fan_speed_pct.toFixed(0)}%` : '—'}</span>
                          <span>{g.live.clock_sm_mhz != null ? `${Math.round(g.live.clock_sm_mhz)} MHz` : '—'}</span>
                          <span>{g.live.power_w != null ? `${g.live.power_w.toFixed(0)}W` : '—'}{g.live.power_limit_w ? `/${Math.round(g.live.power_limit_w)}W` : ''}</span>
                          {g.live.pstate && <code>{g.live.pstate}</code>}
                        </div>
                      )}

                      <div className="gcc-gpu-stat-grid">
                        <div>
                          <span>{t('溫度 (均/P95/峰)', 'Temp (avg/p95/max)')}</span>
                          <strong>{g.avg_temp_c ?? '—'}° / {g.p95_temp_c ?? '—'}° / {g.max_temp_c ?? '—'}°C</strong>
                        </div>
                        <div>
                          <span>{t('負載 (均/P95/峰)', 'Util (avg/p95/max)')}</span>
                          <strong>{g.avg_util ?? '—'}% / {g.p95_util ?? '—'}% / {g.max_util ?? '—'}%</strong>
                        </div>
                        <div>
                          <span>{t('顯存 (均/峰)', 'VRAM (avg/peak)')}</span>
                          <strong>
                            {g.avg_vram_pct ?? '—'}% / {g.max_vram_pct ?? '—'}%
                            {g.peak_vram_gb != null && g.total_vram_gb != null && ` (${g.peak_vram_gb}/${g.total_vram_gb}G)`}
                          </strong>
                        </div>
                        <div>
                          <span>{t('風扇 / 時脈均値', 'Avg Fan / Clock')}</span>
                          <strong>
                            {g.avg_fan_pct != null ? `${g.avg_fan_pct.toFixed(0)}%` : '—'} · {g.avg_clock_mhz != null ? `${Math.round(g.avg_clock_mhz)} MHz` : '—'}
                          </strong>
                        </div>
                      </div>

                      <div className="gcc-stacked-bar" title={`Cool ${g.cool_pct}% · Warm ${g.warm_pct}% · Hot ${g.hot_pct}%`}>
                        <span className="is-cool" style={{ width: `${g.cool_pct}%` }} />
                        <span className="is-warm" style={{ width: `${g.warm_pct}%` }} />
                        <span className="is-hot" style={{ width: `${g.hot_pct}%` }} />
                      </div>
                    </article>
                  ))}
                </div>
              ) : (
                <Empty icon={<Pulse size={24} />} title={t('尚無顯示卡歷史資料', 'No GPU diagnostic history yet')}>
                  {t('等待主機 Agent 回報硬體遙測後，將自動建立每張顯示卡的長期健康檔案。', 'Per-GPU scorecards appear once host hardware telemetry samples are collected.')}
                </Empty>
              )}
            </div>
          </section>

          {/* Backend Anomaly & Ops Audit Log */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('後台異常告警與維運追蹤日誌', 'Backend Anomaly & Ops Audit Log')}</h2>
                <p>
                  {t(
                    '系統自動偵測高溫、顯存滿載、功耗觸頂、CPU/RAM 壓力與維護切換（防洗版節流 5 分鐘），亦可手動紀錄硬體變更。',
                    'Automatically captures debounced thermal, VRAM, power, host pressure, and maintenance transitions alongside manual ops notes.',
                  )}
                </p>
              </div>
              <div className="gcc-energy-card-actions">
                <div className="gcc-range-group" role="group" aria-label={t('篩選事件等級', 'Filter severity')}>
                  {(['all', 'critical', 'warning', 'info'] as const).map(sev => (
                    <button
                      key={sev}
                      type="button"
                      className={`gcc-range-btn${sevFilter === sev ? ' is-active' : ''}`}
                      onClick={() => setSevFilter(sev)}
                    >
                      {sev === 'all' ? t('全部', 'All') : sev === 'critical' ? t('危急', 'Crit') : sev === 'warning' ? t('警戒', 'Warn') : t('資訊', 'Info')}
                    </button>
                  ))}
                </div>
                {unackedTotal > 0 && (
                  <button
                    type="button"
                    className="gcc-button gcc-btn-sm"
                    disabled={busyEventId === '__all__'}
                    onClick={() => void ackAll()}
                  >
                    <Check size={14} />
                    {t('全部已讀', 'Ack All')}
                  </button>
                )}
                <a className="gcc-button gcc-button--quiet gcc-btn-sm" href={exportUrl('events')} download>
                  <DownloadSimple size={15} />
                  CSV
                </a>
              </div>
            </div>
            <div className="gcc-energy-card-body">
              {filteredEvents.length > 0 ? (
                <div className="gcc-bill-table-wrap">
                  <table className="gcc-bill-table">
                    <thead>
                      <tr>
                        <th>{t('時間', 'Timestamp')}</th>
                        <th>{t('等級 / 類型', 'Severity / Type')}</th>
                        <th>{t('主機 / 裝置', 'Host / Device')}</th>
                        <th>{t('事件內容', 'Details')}</th>
                        <th>{t('狀態', 'Status')}</th>
                        <th aria-label={t('操作', 'Actions')} />
                      </tr>
                    </thead>
                    <tbody>
                      {filteredEvents.map(ev => (
                        <tr key={ev.id} className={ev.acknowledged ? 'is-acked-row' : ''}>
                          <td className="gcc-mono-cell">{formatTime(ev.recorded_at)}</td>
                          <td>
                            <div className="gcc-ev-badges">
                              <span className={`gcc-sev-pill is-${ev.severity}`}>
                                {ev.severity === 'critical' ? t('危急', 'CRIT') : ev.severity === 'warning' ? t('警戒', 'WARN') : t('資訊', 'INFO')}
                              </span>
                              <span className="gcc-badge">{eventTypeLabel(ev.event_type)}</span>
                            </div>
                          </td>
                          <td className="gcc-mono-cell">
                            <strong>{ev.node_name}</strong>
                            {ev.gpu_index !== null && <small>GPU {String(ev.gpu_index).padStart(2, '0')}</small>}
                          </td>
                          <td>
                            <span>{ev.message}</span>
                            {ev.metric_value !== null && ev.threshold !== null && (
                              <small className="gcc-mono-sub">
                                {ev.metric_name}: {ev.metric_value} (threshold {ev.threshold})
                              </small>
                            )}
                          </td>
                          <td>
                            <span className={`gcc-badge ${ev.acknowledged ? 'gcc-badge--completed' : 'gcc-badge--queued'}`}>
                              {ev.acknowledged ? t('已確認', 'Acked') : t('待處理', 'Open')}
                            </span>
                          </td>
                          <td className="gcc-bill-actions">
                            <button
                              type="button"
                              className="gcc-icon-button"
                              title={ev.acknowledged ? t('標記為未處理', 'Mark open') : t('標記已處理', 'Acknowledge')}
                              aria-label={ev.acknowledged ? t('標記為未處理', 'Mark open') : t('標記已處理', 'Acknowledge')}
                              disabled={busyEventId === ev.id}
                              onClick={() => void toggleAck(ev.id, !ev.acknowledged)}
                            >
                              <Check size={15} />
                            </button>
                            <button
                              type="button"
                              className="gcc-icon-button gcc-icon-danger"
                              title={t('刪除紀錄', 'Delete event')}
                              aria-label={t('刪除紀錄', 'Delete event')}
                              disabled={busyEventId === ev.id}
                              onClick={() => void deleteEvent(ev.id)}
                            >
                              {busyEventId === ev.id ? <CircleNotch size={15} className="gcc-spin" /> : <Trash size={15} />}
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <Empty
                  icon={<Bell size={26} />}
                  title={t('目前沒有符合條件的硬體事件', 'No hardware events match this filter')}
                  action={
                    <button type="button" className="gcc-button gcc-button--primary" onClick={() => setAddingNote(true)}>
                      <Plus size={16} />
                      {t('新增硬體維運備註', 'Add Ops Log Entry')}
                    </button>
                  }
                >
                  {t(
                    '當出現高溫（≥76°C）、顯存滿載（≥88%）或手動登錄硬體保養／驅動更新時，皆會自動留存於此追蹤日誌。',
                    'Thermal alerts (≥76°C), VRAM pressure (≥88%), maintenance toggles, and manual ops notes appear here.',
                  )}
                </Empty>
              )}
            </div>
          </section>
        </>
      )}

      {addingNote && (
        <HardwareOpsNoteDialog
          nodes={analytics?.available_nodes || nodes.map(n => ({ id: n.id, name: n.name }))}
          gpuIndices={analytics?.available_gpu_indices || [0, 1]}
          onClose={() => setAddingNote(false)}
          onSaved={async () => {
            setAddingNote(false);
            await loadAnalytics();
          }}
        />
      )}
    </section>
  );
}

function HardwareOpsNoteDialog({
  nodes,
  gpuIndices,
  onClose,
  onSaved,
}: {
  nodes: { id: string; name: string }[];
  gpuIndices: number[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [nodeId, setNodeId] = useState(nodes[0]?.id || 'local-host');
  const [gpuIndex, setGpuIndex] = useState('');
  const [severity, setSeverity] = useState<'info' | 'warning' | 'critical'>('info');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api('/hardware/events', 'POST', {
        node_id: nodeId,
        gpu_index: gpuIndex === '' ? null : Number(gpuIndex),
        severity,
        event_type: 'manual_note',
        message: message.trim(),
      });
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('儲存失敗', 'Failed to save log entry'));
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="gcc-dialog gcc-create-dialog" aria-labelledby="hw-note-title" onCancel={e => { if (busy) e.preventDefault(); else onClose(); }}>
      <form onSubmit={e => void submit(e)}>
        <div className="gcc-dialog-head">
          <span><Pulse size={17} />{t('新增硬體維運紀錄', 'Add Hardware Ops Log')}</span>
          <button type="button" className="gcc-icon-button" aria-label={t('關閉', 'Close')} onClick={onClose} disabled={busy}><X size={20} /></button>
        </div>
        <div className="gcc-create-body">
          <h2 id="hw-note-title">{t('記錄硬體變更、保養或異常備註', 'Log Hardware Service or Observation')}</h2>
          <p className="gcc-create-description">
            {t('留存驅動版本更新、清灰換散熱膏、電源限制調整或異常排查紀錄，方便日後對照溫度與負載曲線。', 'Record driver upgrades, thermal maintenance, power-limit changes, or incident notes for historical correlation.')}
          </p>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('目標主機', 'Host')}</span>
              {nodes.length > 0 ? (
                <select value={nodeId} onChange={e => setNodeId(e.target.value)} required>
                  {nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}
                </select>
              ) : (
                <input value={nodeId} onChange={e => setNodeId(e.target.value)} required maxLength={100} />
              )}
            </label>
            <label className="gcc-field">
              <span>{t('關聯顯示卡 (選填)', 'GPU (Optional)')}</span>
              <select value={gpuIndex} onChange={e => setGpuIndex(e.target.value)}>
                <option value="">{t('整機主機', 'Host-wide')}</option>
                {gpuIndices.map(i => <option key={i} value={String(i)}>GPU {String(i).padStart(2, '0')}</option>)}
              </select>
            </label>
          </div>

          <label className="gcc-field">
            <span>{t('事件等級', 'Severity')}</span>
            <select value={severity} onChange={e => setSeverity(e.target.value as 'info' | 'warning' | 'critical')}>
              <option value="info">{t('資訊（保養／變更紀錄）', 'Info (Maintenance / Change)')}</option>
              <option value="warning">{t('警戒（需持續觀察）', 'Warning (Watch)')}</option>
              <option value="critical">{t('危急（故障／高溫異常）', 'Critical (Incident)')}</option>
            </select>
          </label>

          <label className="gcc-field">
            <span>{t('紀錄內容', 'Log Message')} <small>{t('必填', 'Required')}</small></span>
            <textarea
              rows={4}
              value={message}
              onChange={e => setMessage(e.target.value)}
              required
              maxLength={1000}
              autoFocus
              placeholder={t('例如：將 RTX 4090 GPU 00 功耗上限調整為 380W，並更新 NVIDIA 驅動至 570.124。', 'e.g., Capped RTX 4090 GPU 00 power limit to 380W and updated driver to 570.124.')}
            />
          </label>

          {error && <div className="gcc-alert" role="alert"><WarningCircle size={18} /><p>{error}</p></div>}
        </div>
        <div className="gcc-create-actions">
          <span>{t('將即時寫入後台硬體審計日誌', 'Persisted to backend hardware audit log')}</span>
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={busy}>{t('取消', 'Cancel')}</button>
            <button type="submit" className="gcc-button gcc-button--primary" disabled={busy || !message.trim()}>
              {busy ? <CircleNotch size={16} className="gcc-spin" /> : <CheckCircle size={16} />}
              {t('儲存紀錄', 'Save Log')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
