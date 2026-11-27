import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowDown,
  ArrowUp,
  ChartBar,
  ChartLineUp,
  CheckCircle,
  CircleNotch,
  CurrencyDollar,
  DownloadSimple,
  Fire,
  Gauge,
  GearSix,
  Info,
  Leaf,
  Lightning,
  PencilSimple,
  Plus,
  Receipt,
  Sparkle,
  Trash,
  WarningCircle,
  X,
} from '@phosphor-icons/react';
import {
  api,
  type BillCategory,
  type CurrencyCode,
  type ElectricityBill,
  type EnergyAnalytics,
  type EnergyRange,
  type Node,
  type TariffMode,
  type TariffSetting,
} from '../api';
import { useI18n } from '../i18n';
import {
  BillTrendChart,
  CostStructureDonut,
  PowerCostTrendChart,
  WeeklyLoadHeatmap,
  formatKwh,
  formatMoney,
} from './EnergyCharts';
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

export function EnergyPanel({ nodes }: { nodes: Node[] }) {
  const { t, isZh } = useI18n();
  const [range, setRange] = useState<EnergyRange>('24h');
  const [nodeFilter, setNodeFilter] = useState<string>('');
  const [includeSimulated, setIncludeSimulated] = useState(false);
  const [chartMode, setChartMode] = useState<'power' | 'cost'>('power');
  const [donutMode, setDonutMode] = useState<'tariff' | 'component'>('tariff');
  const [analytics, setAnalytics] = useState<EnergyAnalytics | null>(null);
  const [error, setError] = useState('');
  const [editingTariff, setEditingTariff] = useState(false);
  const [billModal, setBillModal] = useState<{ open: boolean; editing: ElectricityBill | null }>({ open: false, editing: null });
  const [deletingBillId, setDeletingBillId] = useState<string | null>(null);
  const loadingRef = useRef(false);

  const hasDemoNodes = nodes.some(n => n.mode === 'demo' || n.metrics?.simulated);

  const loadAnalytics = useCallback(async () => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    try {
      const qs = new URLSearchParams({ range });
      if (nodeFilter) qs.set('node_id', nodeFilter);
      if (includeSimulated) qs.set('include_simulated', 'true');
      const res = await api<EnergyAnalytics>(`/energy/analytics?${qs.toString()}`);
      setAnalytics(res);
      setError('');
    } catch (e) {
      setError(e instanceof Error ? e.message : t('無法載入電費分析資料', 'Failed to load energy analytics'));
    } finally {
      loadingRef.current = false;
    }
  }, [range, nodeFilter, includeSimulated, t]);

  useEffect(() => {
    void loadAnalytics();
    const id = setInterval(() => void loadAnalytics(), 5000);
    return () => clearInterval(id);
  }, [loadAnalytics]);

  const handleDeleteBill = async (billId: string) => {
    setDeletingBillId(billId);
    try {
      await api(`/energy/bills/${billId}`, 'DELETE');
      await loadAnalytics();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('刪除失敗', 'Delete failed'));
    } finally {
      setDeletingBillId(null);
    }
  };

  const exportUrl = (target: 'timeseries' | 'bills' | 'gpu') => {
    const qs = new URLSearchParams({ target, range });
    if (nodeFilter) qs.set('node_id', nodeFilter);
    if (includeSimulated) qs.set('include_simulated', 'true');
    return `/api/energy/export?${qs.toString()}`;
  };

  const categoryLabel = (cat: string) => {
    switch (cat) {
      case 'utility_bill': return t('正式電費帳單', 'Utility Bill');
      case 'smart_plug': return t('智慧插座量測', 'Smart Plug');
      case 'meter_reading': return t('電表抄表', 'Meter Reading');
      default: return t('其他紀錄', 'Other');
    }
  };

  const tierLabel = (tier: string) => {
    if (tier === 'peak') return t('尖峰時段', 'Peak Tariff');
    if (tier === 'off_peak') return t('離峰時段', 'Off-Peak');
    return t('單一費率', 'Flat Rate');
  };

  if (!analytics && !error) {
    return <Skeleton rows={6} />;
  }

  const cur = analytics?.tariff.currency || 'TWD';
  const sum = analytics?.summary;
  const live = analytics?.live;
  const bills = analytics?.bill_analytics;
  const tariff = analytics?.tariff;

  const donutSlices =
    sum && donutMode === 'tariff'
      ? [
          {
            label: t('尖峰時段電費', 'Peak Tariff Cost'),
            value: sum.peak_cost,
            color: 'var(--color-warning)',
            sublabel: `${formatKwh(sum.peak_kwh)} · ${formatMoney(sum.peak_cost, cur, 2)}`,
          },
          {
            label: t('離峰／一般電費', 'Off-Peak / Base Cost'),
            value: sum.off_peak_cost,
            color: 'var(--color-accent)',
            sublabel: `${formatKwh(sum.off_peak_kwh)} · ${formatMoney(sum.off_peak_cost, cur, 2)}`,
          },
        ]
      : sum
        ? [
            {
              label: t('GPU 運算功耗', 'GPU Compute Power'),
              value: sum.gpu_kwh,
              color: 'var(--color-accent)',
              sublabel: formatKwh(sum.gpu_kwh),
            },
            {
              label: t('主機基礎負載 (CPU/RAM)', 'Host Base Load'),
              value: sum.host_overhead_kwh,
              color: 'var(--color-info)',
              sublabel: formatKwh(sum.host_overhead_kwh),
            },
            {
              label: t('電源轉換損耗 (PSU/PUE)', 'PSU Conversion Loss'),
              value: sum.psu_loss_kwh,
              color: 'var(--color-purple)',
              sublabel: formatKwh(sum.psu_loss_kwh),
            },
          ]
        : [];

  return (
    <section className="gcc-energy" aria-label={t('電費與能耗分析儀表板', 'Energy and Electricity Cost Analytics')}>
      {/* Control Toolbar */}
      <div className="gcc-energy-toolbar">
        <div className="gcc-energy-toolbar-left">
          <div className="gcc-range-group" role="group" aria-label={t('分析時間區間', 'Analysis time range')}>
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
          <button type="button" className="gcc-button" onClick={() => setEditingTariff(true)}>
            <GearSix size={16} />
            {t('費率與參數', 'Tariff & PUE')}
          </button>
          <a className="gcc-button" href={exportUrl('timeseries')} download>
            <DownloadSimple size={16} />
            {t('匯出 CSV', 'Export CSV')}
          </a>
          <button
            type="button"
            className="gcc-button gcc-button--primary"
            onClick={() => setBillModal({ open: true, editing: null })}
          >
            <Plus size={16} />
            {t('新增電費紀錄', 'Record Bill')}
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

      {analytics && sum && live && bills && tariff && (
        <>
          {sum.has_simulated && (
            <div className="gcc-energy-sim-banner">
              <Info size={16} />
              <span>{t('目前包含模擬主機（Simulated）的測試數據；正式電費評估建議關閉「含模擬主機」。', 'Simulated demo telemetry is currently included. Uncheck "Include simulated" for real hardware billing.')}</span>
            </div>
          )}

          {/* 6-Card Executive KPI Grid */}
          <div className="gcc-energy-kpis">
            <article className="gcc-kpi-card is-accent">
              <div className="gcc-kpi-head">
                <span><Lightning size={15} />{t('即時系統總功耗', 'Live System Power')}</span>
                <span className={`gcc-tier-pill is-${live.current_tier}`}>{tierLabel(live.current_tier)}</span>
              </div>
              <div className="gcc-kpi-val">
                <strong>{live.total_power_w.toFixed(0)}</strong>
                <span>W</span>
              </div>
              <p className="gcc-kpi-sub">
                GPU {live.gpu_power_w.toFixed(0)}W + {t('主機', 'Host')} {live.host_overhead_w.toFixed(0)}W + PSU {live.psu_loss_w.toFixed(0)}W
              </p>
            </article>

            <article className="gcc-kpi-card is-warn">
              <div className="gcc-kpi-head">
                <span><CurrencyDollar size={15} />{t('即時電費速率', 'Live Burn Rate')}</span>
                <code>{live.current_rate.toFixed(2)} {cur}/kWh</code>
              </div>
              <div className="gcc-kpi-val">
                <strong>{formatMoney(live.cost_per_hour, cur, 2)}</strong>
                <span>/ {t('時', 'hr')}</span>
              </div>
              <p className="gcc-kpi-sub">
                {t('依目前負載日估', 'At current load')}: {formatMoney(live.cost_per_day, cur, 1)} / {t('日', 'day')}
              </p>
            </article>

            <article className="gcc-kpi-card is-info">
              <div className="gcc-kpi-head">
                <span><ChartBar size={15} />{t('區間累計耗電', 'Period Energy')}</span>
                <code>{sum.observed_hours.toFixed(2)}h</code>
              </div>
              <div className="gcc-kpi-val">
                <strong>{sum.total_kwh >= 10 ? sum.total_kwh.toFixed(2) : sum.total_kwh.toFixed(3)}</strong>
                <span>kWh</span>
              </div>
              <p className="gcc-kpi-sub">
                <Leaf size={13} /> {t('碳排約', 'Est. CO₂e')} {sum.co2_kg.toFixed(2)} kg · {sum.sample_count.toLocaleString()} {t('筆樣本', 'samples')}
              </p>
            </article>

            <article className="gcc-kpi-card is-accent">
              <div className="gcc-kpi-head">
                <span><ChartLineUp size={15} />{t('區間推估電費', 'Estimated Cost')}</span>
                <code>{t('均價', 'Avg')} {sum.effective_rate.toFixed(2)}</code>
              </div>
              <div className="gcc-kpi-val">
                <strong>{formatMoney(sum.total_cost, cur, 2)}</strong>
              </div>
              <p className="gcc-kpi-sub">
                {t('尖峰', 'Peak')} {formatMoney(sum.peak_cost, cur, 2)} · {t('離峰', 'Off-Peak')} {formatMoney(sum.off_peak_cost, cur, 2)}
              </p>
            </article>

            <article className="gcc-kpi-card is-purple">
              <div className="gcc-kpi-head">
                <span><Gauge size={15} />{t('30天月電費預測', '30-Day Forecast')}</span>
                {sum.forecast.budget_utilization_pct !== null && (
                  <code className={sum.forecast.budget_utilization_pct > 100 ? 'is-over' : ''}>
                    {sum.forecast.budget_utilization_pct.toFixed(0)}% {t('預算', 'budget')}
                  </code>
                )}
              </div>
              <div className="gcc-kpi-val">
                <strong>{formatMoney(sum.forecast.projected_30d_cost, cur, 0)}</strong>
                <span>/ {t('月', 'mo')}</span>
              </div>
              {sum.forecast.monthly_budget ? (
                <div className="gcc-budget-bar-wrap">
                  <div className="gcc-budget-bar">
                    <span
                      className={sum.forecast.budget_utilization_pct && sum.forecast.budget_utilization_pct > 100 ? 'is-over' : ''}
                      style={{ width: `${Math.min(100, sum.forecast.budget_utilization_pct || 0)}%` }}
                    />
                  </div>
                  <small>{sum.forecast.projected_30d_kwh.toFixed(0)} kWh · {t('預算', 'Budget')} {formatMoney(sum.forecast.monthly_budget, cur, 0)}</small>
                </div>
              ) : (
                <p className="gcc-kpi-sub">{sum.forecast.projected_30d_kwh.toFixed(1)} kWh · {sum.forecast.projected_30d_co2_kg.toFixed(1)} kg CO₂e</p>
              )}
            </article>

            <article className="gcc-kpi-card">
              <div className="gcc-kpi-head">
                <span><Receipt size={15} />{t('帳單紀錄累計', 'Recorded Bills')}</span>
                {bills.mom_cost_change_pct !== null && (
                  <span className={`gcc-mom-pill ${bills.mom_cost_change_pct > 0 ? 'is-up' : 'is-down'}`}>
                    {bills.mom_cost_change_pct > 0 ? <ArrowUp size={11} /> : <ArrowDown size={11} />}
                    {Math.abs(bills.mom_cost_change_pct).toFixed(1)}%
                  </span>
                )}
              </div>
              <div className="gcc-kpi-val">
                <strong>{bills.count > 0 ? formatMoney(bills.total_cost, cur, 0) : '—'}</strong>
                {bills.count > 0 && <span>{bills.count} {t('筆', 'recs')}</span>}
              </div>
              <p className="gcc-kpi-sub">
                {bills.count > 0
                  ? `${bills.total_kwh.toFixed(1)} kWh · ${t('實際均價', 'Actual avg')} ${bills.avg_unit_rate.toFixed(2)} ${cur}`
                  : t('尚未建立手動電費紀錄', 'No manual bills recorded yet')}
              </p>
            </article>
          </div>

          {/* Primary Interactive Time-Series Chart */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('功耗與電費趨勢分析', 'Power & Cost Time-Series Analytics')}</h2>
                <p>
                  {t(
                    '整合各主機 GPU 實測瓦數、主機基礎負載與 PSU PUE 係數，依時段電價即時核算。',
                    'Integrates live per-GPU wattage, host baseline load, and PSU PUE factor priced by real-time tariff tiers.',
                  )}
                </p>
              </div>
              <div className="gcc-chart-mode-switch" role="group" aria-label={t('切換圖表模式', 'Switch chart mode')}>
                <button
                  type="button"
                  className={chartMode === 'power' ? 'is-active' : ''}
                  onClick={() => setChartMode('power')}
                >
                  <ChartLineUp size={15} />
                  {t('功率曲線 (W)', 'Power (W)')}
                </button>
                <button
                  type="button"
                  className={chartMode === 'cost' ? 'is-active' : ''}
                  onClick={() => setChartMode('cost')}
                >
                  <ChartBar size={15} />
                  {t('電費分布', 'Cost Bars')}
                </button>
              </div>
            </div>
            <div className="gcc-energy-card-body">
              <PowerCostTrendChart
                points={analytics.timeseries}
                currency={cur}
                p95W={sum.stats.p95_w}
                meanW={sum.stats.mean_w}
                mode={chartMode}
              />
            </div>
          </section>

          {/* Deep Analytics 2-Column Row: Cost Structure + Statistical Profile & Insights */}
          <div className="gcc-energy-two-col">
            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('成本結構與費率方案比較', 'Cost Structure & Tariff Comparison')}</h2>
                  <p>{t('分析尖離峰佔比與硬體損耗，模擬時間電價 vs 單一費率差異。', 'Break down tariff tiers & hardware losses and compare TOU vs Flat pricing.')}</p>
                </div>
                <div className="gcc-chart-mode-switch">
                  <button type="button" className={donutMode === 'tariff' ? 'is-active' : ''} onClick={() => setDonutMode('tariff')}>
                    {t('尖離峰', 'Tariff')}
                  </button>
                  <button type="button" className={donutMode === 'component' ? 'is-active' : ''} onClick={() => setDonutMode('component')}>
                    {t('能耗組成', 'Components')}
                  </button>
                </div>
              </div>
              <div className="gcc-energy-card-body">
                <CostStructureDonut
                  slices={donutSlices}
                  centerValue={donutMode === 'tariff' ? formatMoney(sum.total_cost, cur, 2) : formatKwh(sum.total_kwh)}
                  centerLabel={donutMode === 'tariff' ? t('區間總電費', 'Period Cost') : t('區間總用電', 'Period Energy')}
                />

                {/* Flat vs TOU Tariff Simulator */}
                <div className="gcc-tariff-compare">
                  <h4>{t('費率方案交叉模擬 (What-If)', 'Tariff Scheme Cross-Simulation')}</h4>
                  <div className="gcc-tariff-compare-grid">
                    <div className={`gcc-tariff-box${tariff.tariff_mode !== 'flat' ? ' is-current' : ''}`}>
                      <span>{t('二段式時間電價 (TOU)', 'Time-of-Use (TOU)')}</span>
                      <strong>{formatMoney(sum.tou_equivalent_cost, cur, 2)}</strong>
                      <small>{t('尖峰', 'Peak')} {tariff.peak_rate} / {t('離峰', 'Off')} {tariff.off_peak_rate}</small>
                    </div>
                    <div className={`gcc-tariff-box${tariff.tariff_mode === 'flat' ? ' is-current' : ''}`}>
                      <span>{t('單一費率 (Flat)', 'Flat Rate')}</span>
                      <strong>{formatMoney(sum.flat_equivalent_cost, cur, 2)}</strong>
                      <small>{tariff.flat_rate} {cur}/kWh</small>
                    </div>
                    <div className="gcc-tariff-box is-saving">
                      <span>{t('移轉 50% 尖峰至離峰可省', '50% Peak-Shift Savings')}</span>
                      <strong>{formatMoney(sum.shift_50_peak_savings, cur, 2)}</strong>
                      <small>{t('本區間估算節費空間', 'Estimated period savings')}</small>
                    </div>
                  </div>
                </div>
              </div>
            </section>

            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('統計分位數與智慧洞察', 'Statistical Profile & Automated Insights')}</h2>
                  <p>{t('專業功耗分布指標與自動異常／節費建議。', 'Power distribution quantiles and data-backed optimization findings.')}</p>
                </div>
              </div>
              <div className="gcc-energy-card-body">
                <div className="gcc-stats-matrix">
                  <div>
                    <span>{t('平均 Mean', 'Mean')}</span>
                    <strong>{sum.stats.mean_w.toFixed(0)}<small>W</small></strong>
                  </div>
                  <div>
                    <span>{t('中位數 P50', 'Median P50')}</span>
                    <strong>{sum.stats.median_w.toFixed(0)}<small>W</small></strong>
                  </div>
                  <div className="is-warn">
                    <span>{t('95分位 P95', 'P95 Power')}</span>
                    <strong>{sum.stats.p95_w.toFixed(0)}<small>W</small></strong>
                  </div>
                  <div>
                    <span>{t('峰值 Max', 'Peak Max')}</span>
                    <strong>{sum.stats.max_w.toFixed(0)}<small>W</small></strong>
                  </div>
                  <div>
                    <span>{t('標準差 σ', 'Std Dev σ')}</span>
                    <strong>±{sum.stats.std_w.toFixed(0)}<small>W</small></strong>
                  </div>
                  <div className="is-accent">
                    <span>{t('負載因數 LF', 'Load Factor')}</span>
                    <strong>{(sum.stats.load_factor * 100).toFixed(1)}<small>%</small></strong>
                  </div>
                </div>

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
              </div>
            </section>
          </div>

          {/* 7x24 Load & Tariff Heatmap */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('24小時 × 7天 用電時段熱力圖', '24×7 Hourly Load & Tariff Heatmap')}</h2>
                <p>{t('辨識高功耗尖峰重疊時段，協助規劃批次推論與模型訓練排程。', 'Identify high-wattage overlap with peak tariff hours to schedule batch compute cheaply.')}</p>
              </div>
            </div>
            <div className="gcc-energy-card-body">
              <WeeklyLoadHeatmap
                days={analytics.heatmap.days}
                maxW={analytics.heatmap.max_w}
                currency={cur}
              />
            </div>
          </section>

          {/* 2-Column Attribution Row: Per-GPU Breakdown & Model Cost Attribution */}
          <div className="gcc-energy-two-col">
            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('各顯示卡能耗與待機浪費分析', 'Per-GPU Power & Idle Waste Analysis')}</h2>
                  <p>{t('比較單卡耗電佔比、平均溫度、利用率與待機電費成本。', 'Compare per-GPU cost share, average load, temperature, and idle power drain.')}</p>
                </div>
                <a className="gcc-button gcc-button--quiet gcc-btn-sm" href={exportUrl('gpu')} download>
                  <DownloadSimple size={15} />
                  CSV
                </a>
              </div>
              <div className="gcc-energy-card-body">
                {analytics.gpu_breakdown.length ? (
                  <div className="gcc-gpu-cost-list">
                    {analytics.gpu_breakdown.map(g => (
                      <div key={`${g.node_id}-${g.gpu_index}`} className="gcc-gpu-cost-row">
                        <div className="gcc-gpu-cost-head">
                          <div>
                            <strong>GPU {String(g.gpu_index).padStart(2, '0')} · {g.gpu_name}</strong>
                            <small>{g.node_name} · {t('均', 'avg')} {g.avg_w.toFixed(0)}W / {t('峰', 'peak')} {g.max_w.toFixed(0)}W</small>
                          </div>
                          <div className="gcc-gpu-cost-right">
                            <strong>{formatMoney(g.cost, cur, 2)}</strong>
                            <small>{formatKwh(g.kwh)} ({g.share_pct.toFixed(1)}%)</small>
                          </div>
                        </div>
                        <div className="gcc-share-bar">
                          <span style={{ width: `${Math.max(2, g.share_pct)}%` }} />
                        </div>
                        <div className="gcc-gpu-cost-meta">
                          <span>{t('均負載', 'Util')}: <b>{g.avg_utilization !== null ? `${g.avg_utilization.toFixed(0)}%` : '—'}</b></span>
                          <span>{t('均溫', 'Temp')}: <b>{g.avg_temperature_c !== null ? `${g.avg_temperature_c.toFixed(0)}°C` : '—'}</b></span>
                          <span className={g.idle_pct >= 50 ? 'is-warn' : ''}>
                            {t('待機佔時', 'Idle')}: <b>{g.idle_pct.toFixed(0)}%</b> ({formatMoney(g.idle_cost, cur, 2)})
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty icon={<Lightning size={24} />} title={t('尚無 GPU 耗電歸因資料', 'No GPU telemetry yet')}>
                    {t('主機 Agent 回報 GPU 功率後，將自動分析每張顯示卡的電費與待機浪費。', 'Per-GPU cost and idle waste analytics appear once host telemetry samples arrive.')}
                  </Empty>
                )}
              </div>
            </section>

            <section className="gcc-panel gcc-energy-card">
              <div className="gcc-panel-head">
                <div>
                  <h2>{t('模型與工作負載電費歸因', 'Workload & Model Cost Attribution')}</h2>
                  <p>{t('追蹤各 AI 模型執行推論所消耗的電量、每任務成本與千字元電費。', 'Track energy and electricity cost attributed to active models, jobs, and token output.')}</p>
                </div>
              </div>
              <div className="gcc-energy-card-body">
                {analytics.model_breakdown.length ? (
                  <div className="gcc-model-cost-list">
                    {analytics.model_breakdown.map(m => (
                      <div key={m.model} className={`gcc-model-cost-row${m.is_idle ? ' is-idle' : ''}`}>
                        <div className="gcc-gpu-cost-head">
                          <div>
                            <strong>{m.is_idle ? t('待機／常駐模型背景功耗', 'Idle / Resident Baseline') : m.model}</strong>
                            <small>
                              {m.hours.toFixed(2)}h · {t('均功', 'avg')} {m.avg_w.toFixed(0)}W
                              {m.job_count > 0 && ` · ${m.job_count} ${t('個任務', 'jobs')}`}
                              {m.total_tokens > 0 && ` · ${m.total_tokens.toLocaleString()} tokens`}
                            </small>
                          </div>
                          <div className="gcc-gpu-cost-right">
                            <strong>{formatMoney(m.cost, cur, 3)}</strong>
                            <small>{formatKwh(m.kwh)} ({m.share_pct.toFixed(1)}%)</small>
                          </div>
                        </div>
                        <div className="gcc-share-bar is-model">
                          <span style={{ width: `${Math.max(2, m.share_pct)}%` }} />
                        </div>
                        {(m.cost_per_job !== null || m.cost_per_1k_tokens !== null) && (
                          <div className="gcc-gpu-cost-meta">
                            {m.cost_per_job !== null && <span>{t('單次任務電費', 'Cost/job')}: <b>{formatMoney(m.cost_per_job, cur, 4)}</b></span>}
                            {m.cost_per_1k_tokens !== null && <span>{t('每 1K tokens', 'Per 1K tokens')}: <b>{formatMoney(m.cost_per_1k_tokens, cur, 5)}</b></span>}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <Empty icon={<ChartBar size={24} />} title={t('尚無工作負載歸因資料', 'No workload breakdown yet')}>
                    {t('執行推論任務後，此處將顯示各模型的電費與每千 token 能耗成本。', 'Run inference jobs to see per-model energy and cost per 1K tokens.')}
                  </Empty>
                )}
              </div>
            </section>
          </div>

          {/* Manual Electricity Bill & Meter Record Ledger + Reconciliation */}
          <section className="gcc-panel gcc-energy-card">
            <div className="gcc-panel-head">
              <div>
                <h2>{t('電費紀錄簿與帳單對帳分析', 'Electricity Bill Ledger & Telemetry Reconciliation')}</h2>
                <p>
                  {t(
                    '手動登錄台電帳單、智慧插座或電表抄表紀錄，系統自動計算實際度電均價並比對同期 GPU 遙測耗電佔比。',
                    'Log utility bills, smart-plug measurements, or meter readings to track actual unit rates and compare against GPU telemetry.',
                  )}
                </p>
              </div>
              <div className="gcc-energy-card-actions">
                <a className="gcc-button gcc-button--quiet gcc-btn-sm" href={exportUrl('bills')} download>
                  <DownloadSimple size={15} />
                  {t('匯出帳單 CSV', 'Bills CSV')}
                </a>
                <button
                  type="button"
                  className="gcc-button gcc-button--primary gcc-btn-sm"
                  onClick={() => setBillModal({ open: true, editing: null })}
                >
                  <Plus size={15} />
                  {t('新增紀錄', 'Add Record')}
                </button>
              </div>
            </div>
            <div className="gcc-energy-card-body">
              {bills.bills.length > 0 ? (
                <>
                  <BillTrendChart bills={bills.bills} currency={cur} />
                  <div className="gcc-bill-table-wrap">
                    <table className="gcc-bill-table">
                      <thead>
                        <tr>
                          <th>{t('帳單／紀錄名稱', 'Record / Period')}</th>
                          <th>{t('類型', 'Category')}</th>
                          <th>{t('實際度數 (kWh)', 'Measured kWh')}</th>
                          <th>{t('實際電費 & 均價', 'Actual Cost & Rate')}</th>
                          <th>{t('同期 GPU 遙測對帳', 'Telemetry Reconciliation')}</th>
                          <th aria-label={t('操作', 'Actions')} />
                        </tr>
                      </thead>
                      <tbody>
                        {bills.bills.map(b => (
                          <tr key={b.id}>
                            <td>
                              <strong>{b.label}</strong>
                              <small className="gcc-mono-sub">
                                {b.period_start} → {b.period_end}
                                {b.node_id ? ` · ${b.node_id}` : ''}
                              </small>
                              {b.notes && <small className="gcc-bill-note">{b.notes}</small>}
                            </td>
                            <td>
                              <span className="gcc-badge">{categoryLabel(b.category)}</span>
                            </td>
                            <td className="gcc-mono-cell">
                              <strong>{b.kwh.toFixed(1)} kWh</strong>
                              {(b.peak_kwh != null || b.off_peak_kwh != null) && (
                                <small>
                                  {t('尖', 'P')} {b.peak_kwh ?? 0} / {t('離', 'O')} {b.off_peak_kwh ?? 0}
                                </small>
                              )}
                            </td>
                            <td className="gcc-mono-cell">
                              <strong>{formatMoney(b.cost, b.currency, 0)}</strong>
                              <small>{(b.unit_rate || 0).toFixed(2)} {b.currency}/kWh</small>
                            </td>
                            <td>
                              {b.telemetry_kwh && b.telemetry_kwh > 0 ? (
                                <div className="gcc-recon-cell">
                                  <span>
                                    {formatKwh(b.telemetry_kwh)} ({formatMoney(b.telemetry_cost || 0, b.currency, 1)})
                                  </span>
                                  {b.gpu_share_pct != null && (
                                    <div className="gcc-recon-bar-row">
                                      <div className="gcc-share-bar">
                                        <span style={{ width: `${Math.min(100, b.gpu_share_pct)}%` }} />
                                      </div>
                                      <code>{b.gpu_share_pct.toFixed(1)}%</code>
                                    </div>
                                  )}
                                </div>
                              ) : (
                                <span className="gcc-muted">{t('該期間無遙測資料', 'No overlapping telemetry')}</span>
                              )}
                            </td>
                            <td className="gcc-bill-actions">
                              <button
                                type="button"
                                className="gcc-icon-button"
                                title={t('編輯紀錄', 'Edit record')}
                                aria-label={t('編輯紀錄', 'Edit record')}
                                onClick={() => setBillModal({ open: true, editing: b })}
                              >
                                <PencilSimple size={15} />
                              </button>
                              <button
                                type="button"
                                className="gcc-icon-button gcc-icon-danger"
                                title={t('刪除紀錄', 'Delete record')}
                                aria-label={t('刪除紀錄', 'Delete record')}
                                disabled={deletingBillId === b.id}
                                onClick={() => void handleDeleteBill(b.id)}
                              >
                                {deletingBillId === b.id ? <CircleNotch size={15} className="gcc-spin" /> : <Trash size={15} />}
                              </button>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <Empty
                  icon={<Receipt size={26} />}
                  title={t('尚未登錄電費帳單或電表紀錄', 'No electricity bill records yet')}
                  action={
                    <button
                      type="button"
                      className="gcc-button gcc-button--primary"
                      onClick={() => setBillModal({ open: true, editing: null })}
                    >
                      <Plus size={16} />
                      {t('新增第一筆電費紀錄', 'Add First Bill Record')}
                    </button>
                  }
                >
                  {t(
                    '紀錄每期台電帳單或智慧插座度數後，即可追蹤前期增減趨勢，並自動比對同期 GPU 算力電費佔比。',
                    'Record monthly utility bills or smart-plug readings to track MoM cost trends and reconcile GPU power share.',
                  )}
                </Empty>
              )}
            </div>
          </section>
        </>
      )}

      {editingTariff && tariff && (
        <TariffSettingsDialog
          initial={tariff}
          onClose={() => setEditingTariff(false)}
          onSaved={async () => {
            setEditingTariff(false);
            await loadAnalytics();
          }}
        />
      )}

      {billModal.open && (
        <BillRecordDialog
          initial={billModal.editing}
          defaultCurrency={cur as CurrencyCode}
          nodes={analytics?.available_nodes || nodes.map(n => ({ id: n.id, name: n.name }))}
          onClose={() => setBillModal({ open: false, editing: null })}
          onSaved={async () => {
            setBillModal({ open: false, editing: null });
            await loadAnalytics();
          }}
        />
      )}
    </section>
  );
}

function TariffSettingsDialog({
  initial,
  onClose,
  onSaved,
}: {
  initial: TariffSetting;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [form, setForm] = useState<TariffSetting>({ ...initial });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  const applyPreset = (preset: 'tw_tou' | 'tw_flat' | 'us_flat') => {
    if (preset === 'tw_tou') {
      setForm(f => ({
        ...f,
        currency: 'TWD',
        tariff_mode: 'tou_tw',
        flat_rate: 3.85,
        peak_rate: 5.06,
        off_peak_rate: 2.06,
        non_summer_peak_rate: 4.86,
        non_summer_off_peak_rate: 1.99,
        peak_start_hour: 9,
        peak_end_hour: 24,
        weekend_off_peak: true,
        summer_months: [6, 7, 8, 9],
        timezone_offset_hours: 8,
        co2_kg_per_kwh: 0.494,
      }));
    } else if (preset === 'tw_flat') {
      setForm(f => ({
        ...f,
        currency: 'TWD',
        tariff_mode: 'flat',
        flat_rate: 3.85,
        timezone_offset_hours: 8,
        co2_kg_per_kwh: 0.494,
      }));
    } else {
      setForm(f => ({
        ...f,
        currency: 'USD',
        tariff_mode: 'flat',
        flat_rate: 0.16,
        co2_kg_per_kwh: 0.386,
      }));
    }
  };

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const payload = {
        currency: form.currency,
        tariff_mode: form.tariff_mode,
        flat_rate: Number(form.flat_rate),
        peak_rate: Number(form.peak_rate),
        off_peak_rate: Number(form.off_peak_rate),
        non_summer_peak_rate: Number(form.non_summer_peak_rate),
        non_summer_off_peak_rate: Number(form.non_summer_off_peak_rate),
        peak_start_hour: Number(form.peak_start_hour),
        peak_end_hour: Number(form.peak_end_hour),
        weekend_off_peak: Boolean(form.weekend_off_peak),
        summer_months: form.summer_months,
        timezone_offset_hours: Number(form.timezone_offset_hours),
        pue_factor: Number(form.pue_factor),
        base_host_watts: Number(form.base_host_watts),
        co2_kg_per_kwh: Number(form.co2_kg_per_kwh),
        monthly_budget: form.monthly_budget != null && String(form.monthly_budget) !== '' ? Number(form.monthly_budget) : null,
      };
      await api('/energy/tariff', 'PUT', payload);
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('儲存失敗', 'Failed to save tariff settings'));
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="gcc-dialog gcc-create-dialog" aria-labelledby="tariff-title" onCancel={e => { if (busy) e.preventDefault(); else onClose(); }}>
      <form onSubmit={e => void submit(e)}>
        <div className="gcc-dialog-head">
          <span><GearSix size={17} />{t('電價費率與能耗模型設定', 'Tariff & Energy Model Settings')}</span>
          <button type="button" className="gcc-icon-button" aria-label={t('關閉', 'Close')} onClick={onClose} disabled={busy}><X size={20} /></button>
        </div>
        <div className="gcc-create-body">
          <h2 id="tariff-title">{t('設定電費計算參數', 'Configure Electricity Pricing')}</h2>
          <p className="gcc-create-description">
            {t('修改費率或 PUE 會即時套用至所有歷史電力遙測分析，無需重新採樣。', 'Updating rates or PUE retroactively recalculates all telemetry analytics instantly.')}
          </p>

          <div className="gcc-preset-row">
            <span>{t('快速套用：', 'Presets:')}</span>
            <button type="button" className="gcc-button gcc-btn-sm" onClick={() => applyPreset('tw_tou')}>{t('台電簡易二段式 (TWD)', 'Taipower 2-Tier TOU')}</button>
            <button type="button" className="gcc-button gcc-btn-sm" onClick={() => applyPreset('tw_flat')}>{t('台電單一均價 (TWD)', 'Taipower Flat')}</button>
            <button type="button" className="gcc-button gcc-btn-sm" onClick={() => applyPreset('us_flat')}>{t('US Flat ($0.16)', 'US Flat ($0.16)')}</button>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('計費模式', 'Tariff Mode')}</span>
              <select value={form.tariff_mode} onChange={e => setForm({ ...form, tariff_mode: e.target.value as TariffMode })}>
                <option value="tou_tw">{t('台電二段式時間電價 (夏/非夏月)', 'Taiwan 2-Tier TOU (Summer/Non-Summer)')}</option>
                <option value="tou_custom">{t('自訂尖離峰時間電價', 'Custom Peak / Off-Peak TOU')}</option>
                <option value="flat">{t('單一固定費率', 'Flat Rate')}</option>
              </select>
            </label>
            <label className="gcc-field">
              <span>{t('幣別', 'Currency')}</span>
              <select value={form.currency} onChange={e => setForm({ ...form, currency: e.target.value as CurrencyCode })}>
                <option value="TWD">TWD (NT$)</option>
                <option value="USD">USD ($)</option>
                <option value="EUR">EUR (€)</option>
                <option value="JPY">JPY (¥)</option>
                <option value="CNY">CNY (¥)</option>
              </select>
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('單一費率 / 對照基準', 'Flat / Baseline Rate')} <small>{form.currency}/kWh</small></span>
              <input type="number" step="0.01" min={0} max={1000} value={form.flat_rate} onChange={e => setForm({ ...form, flat_rate: Number(e.target.value) })} required />
            </label>
            <label className="gcc-field">
              <span>{t('每月電費預算目標', 'Monthly Budget Target')} <small>{form.currency}</small></span>
              <input type="number" step="50" min={0} value={form.monthly_budget ?? ''} placeholder={t('留空停用', 'Optional')} onChange={e => setForm({ ...form, monthly_budget: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
          </div>

          {form.tariff_mode !== 'flat' && (
            <>
              <div className="gcc-form-grid">
                <label className="gcc-field">
                  <span>{form.tariff_mode === 'tou_tw' ? t('夏月尖峰單價', 'Summer Peak Rate') : t('尖峰單價', 'Peak Rate')}</span>
                  <input type="number" step="0.01" min={0} value={form.peak_rate} onChange={e => setForm({ ...form, peak_rate: Number(e.target.value) })} required />
                </label>
                <label className="gcc-field">
                  <span>{form.tariff_mode === 'tou_tw' ? t('夏月離峰單價', 'Summer Off-Peak Rate') : t('離峰單價', 'Off-Peak Rate')}</span>
                  <input type="number" step="0.01" min={0} value={form.off_peak_rate} onChange={e => setForm({ ...form, off_peak_rate: Number(e.target.value) })} required />
                </label>
              </div>
              {form.tariff_mode === 'tou_tw' && (
                <div className="gcc-form-grid">
                  <label className="gcc-field">
                    <span>{t('非夏月尖峰單價', 'Non-Summer Peak Rate')}</span>
                    <input type="number" step="0.01" min={0} value={form.non_summer_peak_rate} onChange={e => setForm({ ...form, non_summer_peak_rate: Number(e.target.value) })} required />
                  </label>
                  <label className="gcc-field">
                    <span>{t('非夏月離峰單價', 'Non-Summer Off-Peak Rate')}</span>
                    <input type="number" step="0.01" min={0} value={form.non_summer_off_peak_rate} onChange={e => setForm({ ...form, non_summer_off_peak_rate: Number(e.target.value) })} required />
                  </label>
                </div>
              )}
              <div className="gcc-form-grid">
                <label className="gcc-field">
                  <span>{t('尖峰開始小時 (0-23)', 'Peak Start Hour')}</span>
                  <input type="number" min={0} max={23} value={form.peak_start_hour} onChange={e => setForm({ ...form, peak_start_hour: Number(e.target.value) })} required />
                </label>
                <label className="gcc-field">
                  <span>{t('尖峰結束小時 (1-24)', 'Peak End Hour')}</span>
                  <input type="number" min={1} max={24} value={form.peak_end_hour} onChange={e => setForm({ ...form, peak_end_hour: Number(e.target.value) })} required />
                </label>
              </div>
              <label className="gcc-energy-checkbox">
                <input type="checkbox" checked={form.weekend_off_peak} onChange={e => setForm({ ...form, weekend_off_peak: e.target.checked })} />
                <span>{t('週六、週日全日套用離峰費率', 'Apply off-peak rate all day on weekends')}</span>
              </label>
            </>
          )}

          <div className="gcc-form-grid" style={{ marginTop: 10 }}>
            <label className="gcc-field">
              <span>{t('主機基礎功耗 (不含 GPU)', 'Base Host Overhead')} <small>W</small></span>
              <input type="number" step="5" min={0} max={5000} value={form.base_host_watts} onChange={e => setForm({ ...form, base_host_watts: Number(e.target.value) })} required />
              <span className="gcc-field-help">{t('CPU/主機板/記憶體/風扇功耗，設 0 則僅計 GPU。', 'CPU/RAM/motherboard draw; set 0 for GPU-only.')}</span>
            </label>
            <label className="gcc-field">
              <span>{t('PSU 效率／PUE 係數', 'PSU / PUE Factor')} <small>1.00–3.00</small></span>
              <input type="number" step="0.01" min={1} max={3} value={form.pue_factor} onChange={e => setForm({ ...form, pue_factor: Number(e.target.value) })} required />
              <span className="gcc-field-help">{t('80+ 白金電源轉換損耗約 1.08–1.14。', '80+ Platinum PSU overhead is typically 1.08–1.14.')}</span>
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('電力排碳係數', 'Grid CO₂ Factor')} <small>kg/kWh</small></span>
              <input type="number" step="0.001" min={0} max={5} value={form.co2_kg_per_kwh} onChange={e => setForm({ ...form, co2_kg_per_kwh: Number(e.target.value) })} required />
            </label>
            <label className="gcc-field">
              <span>{t('時區偏移 (UTC±)', 'Timezone Offset (UTC±)')}</span>
              <input type="number" step="0.5" min={-12} max={14} value={form.timezone_offset_hours} onChange={e => setForm({ ...form, timezone_offset_hours: Number(e.target.value) })} required />
            </label>
          </div>

          {error && <div className="gcc-alert" role="alert"><WarningCircle size={18} /><p>{error}</p></div>}
        </div>
        <div className="gcc-create-actions">
          <span>{t('公式：總瓦數 = (GPU + 基礎) × PUE', 'Formula: Total W = (GPU + Base) × PUE')}</span>
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={busy}>{t('取消', 'Cancel')}</button>
            <button type="submit" className="gcc-button gcc-button--primary" disabled={busy}>
              {busy ? <CircleNotch size={16} className="gcc-spin" /> : <CheckCircle size={16} />}
              {t('儲存設定', 'Save Settings')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}

function BillRecordDialog({
  initial,
  defaultCurrency,
  nodes,
  onClose,
  onSaved,
}: {
  initial: ElectricityBill | null;
  defaultCurrency: CurrencyCode;
  nodes: { id: string; name: string }[];
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const today = new Date().toISOString().slice(0, 10);
  const monthStart = `${today.slice(0, 8)}01`;

  const [label, setLabel] = useState(initial?.label || '');
  const [category, setCategory] = useState<BillCategory>(initial?.category || 'utility_bill');
  const [billingDate, setBillingDate] = useState(initial?.billing_date || today);
  const [periodStart, setPeriodStart] = useState(initial?.period_start || monthStart);
  const [periodEnd, setPeriodEnd] = useState(initial?.period_end || today);
  const [nodeId, setNodeId] = useState(initial?.node_id || '');
  const [kwh, setKwh] = useState(initial ? String(initial.kwh) : '');
  const [cost, setCost] = useState(initial ? String(initial.cost) : '');
  const [currency, setCurrency] = useState<CurrencyCode>(initial?.currency || defaultCurrency);
  const [peakKwh, setPeakKwh] = useState(initial?.peak_kwh != null ? String(initial.peak_kwh) : '');
  const [offPeakKwh, setOffPeakKwh] = useState(initial?.off_peak_kwh != null ? String(initial.off_peak_kwh) : '');
  const [notes, setNotes] = useState(initial?.notes || '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const d = ref.current;
    d?.showModal();
    return () => d?.close();
  }, []);

  const numKwh = Number(kwh);
  const numCost = Number(cost);
  const liveRate = numKwh > 0 && numCost >= 0 ? numCost / numKwh : null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const payload = {
        label: label.trim(),
        category,
        billing_date: billingDate,
        period_start: periodStart,
        period_end: periodEnd,
        node_id: nodeId || null,
        kwh: Number(kwh),
        cost: Number(cost),
        currency,
        peak_kwh: peakKwh.trim() !== '' ? Number(peakKwh) : null,
        off_peak_kwh: offPeakKwh.trim() !== '' ? Number(offPeakKwh) : null,
        notes: notes.trim() || null,
      };
      if (initial) {
        await api(`/energy/bills/${initial.id}`, 'PUT', payload);
      } else {
        await api('/energy/bills', 'POST', payload);
      }
      await onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : t('儲存紀錄失敗', 'Failed to save bill record'));
      setBusy(false);
    }
  };

  return (
    <dialog ref={ref} className="gcc-dialog gcc-create-dialog" aria-labelledby="bill-title" onCancel={e => { if (busy) e.preventDefault(); else onClose(); }}>
      <form onSubmit={e => void submit(e)}>
        <div className="gcc-dialog-head">
          <span><Receipt size={17} />{initial ? t('編輯電費紀錄', 'Edit Bill Record') : t('新增電費紀錄', 'Record Electricity Bill')}</span>
          <button type="button" className="gcc-icon-button" aria-label={t('關閉', 'Close')} onClick={onClose} disabled={busy}><X size={20} /></button>
        </div>
        <div className="gcc-create-body">
          <h2 id="bill-title">{initial ? t('修改帳單／電表資料', 'Update Bill / Meter Entry') : t('登錄實際電費或電表度數', 'Log Actual Bill or Meter Reading')}</h2>
          <p className="gcc-create-description">
            {t('輸入台電帳單或智慧插座量測結果，系統將自動計算均價並與 GPU 遙測對帳。', 'Record actual kWh & cost to analyze unit price trends and reconcile GPU power share.')}
          </p>

          <label className="gcc-field">
            <span>{t('紀錄名稱', 'Label')} <small>{t('必填', 'Required')}</small></span>
            <input value={label} onChange={e => setLabel(e.target.value)} required maxLength={120} placeholder={t('例如：2026年11月台電帳單 / RTX 4090 智慧插座週報', 'e.g., Nov 2026 Utility Bill / Workstation Smart Plug')} autoFocus />
          </label>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('紀錄類型', 'Category')}</span>
              <select value={category} onChange={e => setCategory(e.target.value as BillCategory)}>
                <option value="utility_bill">{t('正式電費帳單', 'Utility Bill')}</option>
                <option value="smart_plug">{t('智慧插座量測', 'Smart Plug Reading')}</option>
                <option value="meter_reading">{t('電表抄表紀錄', 'Meter Reading')}</option>
                <option value="other">{t('其他', 'Other')}</option>
              </select>
            </label>
            <label className="gcc-field">
              <span>{t('關聯主機 (選填)', 'Linked Host (Optional)')}</span>
              <select value={nodeId} onChange={e => setNodeId(e.target.value)}>
                <option value="">{t('全站／未指定', 'All / Unspecified')}</option>
                {nodes.map(n => (
                  <option key={n.id} value={n.id}>{n.name}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('計費起日', 'Period Start')}</span>
              <input type="date" value={periodStart} onChange={e => setPeriodStart(e.target.value)} required />
            </label>
            <label className="gcc-field">
              <span>{t('計費迄日', 'Period End')}</span>
              <input type="date" value={periodEnd} onChange={e => { setPeriodEnd(e.target.value); if (!initial) setBillingDate(e.target.value); }} required />
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('實際用電度數 (kWh)', 'Measured Energy (kWh)')} <small>{t('必填', 'Required')}</small></span>
              <input type="number" step="0.01" min={0.01} value={kwh} onChange={e => setKwh(e.target.value)} required placeholder="420.5" />
            </label>
            <label className="gcc-field">
              <span>{t('實際電費金額', 'Actual Cost')} <small>{currency}</small></span>
              <input type="number" step="0.01" min={0} value={cost} onChange={e => setCost(e.target.value)} required placeholder="1680" />
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('入帳／抄表日期', 'Billing Date')}</span>
              <input type="date" value={billingDate} onChange={e => setBillingDate(e.target.value)} required />
            </label>
            <label className="gcc-field">
              <span>{t('幣別', 'Currency')}</span>
              <select value={currency} onChange={e => setCurrency(e.target.value as CurrencyCode)}>
                <option value="TWD">TWD (NT$)</option>
                <option value="USD">USD ($)</option>
                <option value="EUR">EUR (€)</option>
                <option value="JPY">JPY (¥)</option>
                <option value="CNY">CNY (¥)</option>
              </select>
            </label>
          </div>

          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('尖峰度數 (選填)', 'Peak kWh (Optional)')}</span>
              <input type="number" step="0.1" min={0} value={peakKwh} onChange={e => setPeakKwh(e.target.value)} placeholder="—" />
            </label>
            <label className="gcc-field">
              <span>{t('離峰度數 (選填)', 'Off-Peak kWh (Optional)')}</span>
              <input type="number" step="0.1" min={0} value={offPeakKwh} onChange={e => setOffPeakKwh(e.target.value)} placeholder="—" />
            </label>
          </div>

          <label className="gcc-field">
            <span>{t('備註', 'Notes')}</span>
            <input value={notes} onChange={e => setNotes(e.target.value)} maxLength={1000} placeholder={t('例如：含兩張 RTX 4090 連續訓練 48 小時', 'e.g., Includes 48h dual RTX 4090 training run')} />
          </label>

          {error && <div className="gcc-alert" role="alert"><WarningCircle size={18} /><p>{error}</p></div>}
        </div>
        <div className="gcc-create-actions">
          <span>
            {liveRate !== null
              ? `${t('換算平均單價', 'Implied unit rate')}: ${liveRate.toFixed(2)} ${currency}/kWh`
              : t('輸入度數與金額後自動換算單價', 'Enter kWh and cost to preview unit rate')}
          </span>
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={busy}>{t('取消', 'Cancel')}</button>
            <button type="submit" className="gcc-button gcc-button--primary" disabled={busy}>
              {busy ? <CircleNotch size={16} className="gcc-spin" /> : <CheckCircle size={16} />}
              {initial ? t('儲存修改', 'Save Changes') : t('建立紀錄', 'Save Record')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
