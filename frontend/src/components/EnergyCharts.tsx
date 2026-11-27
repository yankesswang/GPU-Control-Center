import { useMemo, useState } from 'react';
import {
  type CurrencyCode,
  type ElectricityBill,
  type EnergyHeatmapCell,
  type EnergyTimeseriesPoint,
} from '../api';
import { useI18n } from '../i18n';

const CURRENCY_PREFIX: Record<CurrencyCode | string, string> = {
  TWD: 'NT$',
  USD: '$',
  EUR: '€',
  JPY: '¥',
  CNY: '¥',
};

export function formatMoney(amount: number, currency: string, decimals = 2): string {
  const prefix = CURRENCY_PREFIX[currency] || `${currency} `;
  if (!Number.isFinite(amount)) return `${prefix}0`;
  const abs = Math.abs(amount);
  const d = abs >= 1000 ? Math.min(decimals, 0) : abs >= 100 ? Math.min(decimals, 1) : decimals;
  return `${amount < 0 ? '-' : ''}${prefix}${abs.toLocaleString('en-US', {
    minimumFractionDigits: d,
    maximumFractionDigits: d,
  })}`;
}

export function formatKwh(kwh: number): string {
  if (!Number.isFinite(kwh)) return '0.00 kWh';
  if (kwh >= 100) return `${kwh.toFixed(1)} kWh`;
  if (kwh >= 1) return `${kwh.toFixed(2)} kWh`;
  return `${kwh.toFixed(3)} kWh`;
}

interface TrendProps {
  points: EnergyTimeseriesPoint[];
  currency: string;
  p95W: number;
  meanW: number;
  mode: 'power' | 'cost';
}

export function PowerCostTrendChart({ points, currency, p95W, meanW, mode }: TrendProps) {
  const { t } = useI18n();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const W = 820;
  const H = 268;
  const pad = { top: 20, right: 20, bottom: 34, left: 56 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  const { maxY, ticks } = useMemo(() => {
    if (!points.length) return { maxY: 100, ticks: [0, 25, 50, 75, 100] };
    const rawMax =
      mode === 'power'
        ? Math.max(...points.map(p => Math.max(p.max_total_w, p.avg_total_w)), p95W, 50)
        : Math.max(...points.map(p => p.cost), 0.01);
    const niceMax = niceCeiling(rawMax * 1.12);
    return {
      maxY: niceMax,
      ticks: [0, 0.25, 0.5, 0.75, 1].map(f => niceMax * f),
    };
  }, [points, mode, p95W]);

  if (!points.length) {
    return (
      <div className="gcc-chart-empty">
        <p>{t('目前選取區間尚無電力遙測資料，Agent 連線後每 4 秒自動紀錄。', 'No power telemetry samples in this range yet. Samples record automatically every 4 seconds once an agent connects.')}</p>
      </div>
    );
  }

  const n = points.length;
  const xFor = (i: number) =>
    n === 1 ? pad.left + plotW / 2 : pad.left + (i / (n - 1)) * plotW;
  const yFor = (val: number) =>
    pad.top + plotH - Math.min(1, Math.max(0, val / (maxY || 1))) * plotH;

  const totalLine = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(i).toFixed(1)},${yFor(p.avg_total_w).toFixed(1)}`).join(' ');
  const gpuLine = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(i).toFixed(1)},${yFor(p.avg_gpu_w).toFixed(1)}`).join(' ');
  const areaPath =
    n > 1
      ? `${totalLine} L${xFor(n - 1).toFixed(1)},${(pad.top + plotH).toFixed(1)} L${xFor(0).toFixed(1)},${(pad.top + plotH).toFixed(1)} Z`
      : '';
  const bandPath =
    n > 1
      ? points.map((p, i) => `${i === 0 ? 'M' : 'L'}${xFor(i).toFixed(1)},${yFor(p.max_total_w).toFixed(1)}`).join(' ') +
        ' ' +
        points
          .slice()
          .reverse()
          .map((p, ri) => `L${xFor(n - 1 - ri).toFixed(1)},${yFor(p.min_total_w).toFixed(1)}`)
          .join(' ') +
        ' Z'
      : '';

  const barStep = plotW / Math.max(1, n);
  const barW = Math.max(4, Math.min(28, barStep * 0.66));

  // Choose ~6 clean x-axis labels
  const labelStride = Math.max(1, Math.ceil(n / 6));
  const hovered = hoverIdx !== null && points[hoverIdx] ? points[hoverIdx] : null;
  const hoveredX = hoverIdx !== null ? xFor(hoverIdx) : 0;

  return (
    <div className="gcc-chart-wrap">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="gcc-energy-svg"
        role="img"
        aria-label={mode === 'power' ? t('功率時間序列圖', 'Power time series chart') : t('電費時間序列圖', 'Electricity cost chart')}
        onMouseLeave={() => setHoverIdx(null)}
      >
        <defs>
          <linearGradient id="gccPowerArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.32" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {/* Peak tariff background shading */}
        {points.map((p, i) => {
          if (p.tariff_tier !== 'peak') return null;
          const x0 = n === 1 ? pad.left : Math.max(pad.left, xFor(i) - barStep / 2);
          const x1 = n === 1 ? pad.left + plotW : Math.min(pad.left + plotW, xFor(i) + barStep / 2);
          return (
            <rect
              key={`pk-${i}`}
              x={x0}
              y={pad.top}
              width={Math.max(1, x1 - x0)}
              height={plotH}
              fill="var(--color-warning)"
              fillOpacity="0.055"
            />
          );
        })}

        {/* Horizontal gridlines & Y ticks */}
        {ticks.map((tv, idx) => {
          const y = yFor(tv);
          return (
            <g key={idx}>
              <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} className="gcc-chart-grid" />
              <text x={pad.left - 8} y={y + 4} textAnchor="end" className="gcc-chart-axis-text">
                {mode === 'power' ? `${Math.round(tv)}W` : formatMoney(tv, currency, tv < 1 ? 2 : 1)}
              </text>
            </g>
          );
        })}

        {mode === 'power' ? (
          <>
            {bandPath && <path d={bandPath} fill="var(--color-info)" fillOpacity="0.11" />}
            {areaPath && <path d={areaPath} fill="url(#gccPowerArea)" />}
            {n > 1 ? (
              <>
                <path d={gpuLine} fill="none" stroke="var(--color-info)" strokeWidth="1.6" strokeDasharray="4 3" />
                <path d={totalLine} fill="none" stroke="var(--color-accent)" strokeWidth="2.4" strokeLinejoin="round" />
              </>
            ) : (
              <>
                <circle cx={xFor(0)} cy={yFor(points[0].avg_total_w)} r="5" fill="var(--color-accent)" />
                <circle cx={xFor(0)} cy={yFor(points[0].avg_gpu_w)} r="4" fill="var(--color-info)" />
              </>
            )}

            {/* P95 & Mean reference lines */}
            {p95W > 0 && p95W <= maxY && (
              <g>
                <line x1={pad.left} x2={W - pad.right} y1={yFor(p95W)} y2={yFor(p95W)} stroke="var(--color-warning)" strokeWidth="1" strokeDasharray="3 4" opacity="0.8" />
                <text x={W - pad.right - 4} y={yFor(p95W) - 5} textAnchor="end" className="gcc-chart-ref-label is-warn">
                  P95 {Math.round(p95W)}W
                </text>
              </g>
            )}
            {meanW > 0 && meanW <= maxY && Math.abs(yFor(meanW) - yFor(p95W)) > 16 && (
              <g>
                <line x1={pad.left} x2={W - pad.right} y1={yFor(meanW)} y2={yFor(meanW)} stroke="var(--color-muted)" strokeWidth="1" strokeDasharray="2 4" opacity="0.7" />
                <text x={W - pad.right - 4} y={yFor(meanW) - 4} textAnchor="end" className="gcc-chart-ref-label">
                  AVG {Math.round(meanW)}W
                </text>
              </g>
            )}
          </>
        ) : (
          /* Cost & Energy Bar Mode */
          points.map((p, i) => {
            const cx = n === 1 ? pad.left + plotW / 2 : pad.left + (i + 0.5) * barStep;
            const y = yFor(p.cost);
            const h = Math.max(2, pad.top + plotH - y);
            const fill =
              p.tariff_tier === 'peak'
                ? 'var(--color-warning)'
                : p.tariff_tier === 'off_peak'
                  ? 'var(--color-accent)'
                  : 'var(--color-info)';
            return (
              <rect
                key={`bar-${i}`}
                x={cx - barW / 2}
                y={y}
                width={barW}
                height={h}
                rx="3"
                fill={fill}
                fillOpacity={hoverIdx === null || hoverIdx === i ? 0.88 : 0.35}
              />
            );
          })
        )}

        {/* X axis labels */}
        {points.map((p, i) => {
          if (i % labelStride !== 0 && i !== n - 1) return null;
          const x = mode === 'cost' && n > 1 ? pad.left + (i + 0.5) * barStep : xFor(i);
          return (
            <text key={`xl-${i}`} x={x} y={H - 10} textAnchor="middle" className="gcc-chart-axis-text">
              {p.label}
            </text>
          );
        })}

        {/* Hover crosshair & dot */}
        {hovered && hoverIdx !== null && (
          <g>
            <line
              x1={mode === 'cost' && n > 1 ? pad.left + (hoverIdx + 0.5) * barStep : hoveredX}
              x2={mode === 'cost' && n > 1 ? pad.left + (hoverIdx + 0.5) * barStep : hoveredX}
              y1={pad.top}
              y2={pad.top + plotH}
              stroke="var(--color-ink-2)"
              strokeWidth="1"
              strokeDasharray="3 3"
              opacity="0.65"
            />
            {mode === 'power' && (
              <>
                <circle cx={hoveredX} cy={yFor(hovered.avg_total_w)} r="4.5" fill="var(--color-accent)" stroke="var(--color-paper-2)" strokeWidth="2" />
                <circle cx={hoveredX} cy={yFor(hovered.avg_gpu_w)} r="3.5" fill="var(--color-info)" stroke="var(--color-paper-2)" strokeWidth="1.5" />
              </>
            )}
          </g>
        )}

        {/* Invisible hit zones for smooth hover */}
        {points.map((_, i) => {
          const x0 = n === 1 ? pad.left : mode === 'cost' ? pad.left + i * barStep : Math.max(pad.left, xFor(i) - barStep / 2);
          const w = n === 1 ? plotW : barStep;
          return (
            <rect
              key={`hit-${i}`}
              x={x0}
              y={pad.top}
              width={Math.max(4, w)}
              height={plotH}
              fill="transparent"
              onMouseEnter={() => setHoverIdx(i)}
            />
          );
        })}
      </svg>

      {/* Legend + Live Hover Readout Strip */}
      <div className="gcc-chart-footer">
        <div className="gcc-chart-legend">
          {mode === 'power' ? (
            <>
              <span><i style={{ background: 'var(--color-accent)' }} />{t('總功耗 (含主機+PSU)', 'Total Power (w/ Host+PSU)')}</span>
              <span><i style={{ background: 'var(--color-info)' }} />{t('GPU 實測功耗', 'GPU Measured')}</span>
              <span><i style={{ background: 'var(--color-warning)', opacity: 0.45 }} />{t('尖峰費率區段', 'Peak Tariff Zone')}</span>
            </>
          ) : (
            <>
              <span><i style={{ background: 'var(--color-warning)' }} />{t('尖峰電費', 'Peak Cost')}</span>
              <span><i style={{ background: 'var(--color-accent)' }} />{t('離峰電費', 'Off-Peak Cost')}</span>
              <span><i style={{ background: 'var(--color-info)' }} />{t('單一費率', 'Flat Rate')}</span>
            </>
          )}
        </div>
        {hovered ? (
          <div className="gcc-chart-tooltip-bar">
            <strong>{hovered.label}</strong>
            <span>{t('總功', 'Total')} <b>{hovered.avg_total_w.toFixed(0)}W</b> ({t('峰', 'max')} {hovered.max_total_w.toFixed(0)}W)</span>
            <span>GPU <b>{hovered.avg_gpu_w.toFixed(0)}W</b></span>
            <span><b>{formatKwh(hovered.kwh)}</b></span>
            <span className="is-cost">{formatMoney(hovered.cost, currency, 3)}</span>
            {hovered.avg_utilization !== null && <span>{t('負載', 'Util')} {hovered.avg_utilization.toFixed(0)}%</span>}
          </div>
        ) : (
          <span className="gcc-chart-hint">{t('滑鼠移至圖表檢視各時段明細', 'Hover over chart to inspect bucket telemetry')}</span>
        )}
      </div>
    </div>
  );
}

interface HeatmapProps {
  days: { day: number; hours: EnergyHeatmapCell[] }[];
  maxW: number;
  currency: string;
}

export function WeeklyLoadHeatmap({ days, maxW, currency }: HeatmapProps) {
  const { t } = useI18n();
  const [selected, setSelected] = useState<{ day: number; cell: EnergyHeatmapCell } | null>(null);
  const dayNames = [
    t('週一', 'Mon'),
    t('週二', 'Tue'),
    t('週三', 'Wed'),
    t('週四', 'Thu'),
    t('週五', 'Fri'),
    t('週六', 'Sat'),
    t('週日', 'Sun'),
  ];

  const colorFor = (cell: EnergyHeatmapCell) => {
    if (cell.samples === 0 || maxW <= 0) return 'var(--color-paper)';
    const ratio = Math.min(1, cell.avg_w / maxW);
    if (ratio < 0.25) return 'color-mix(in oklch, var(--color-accent) 26%, var(--color-paper-2))';
    if (ratio < 0.5) return 'color-mix(in oklch, var(--color-accent) 56%, var(--color-paper-2))';
    if (ratio < 0.75) return 'color-mix(in oklch, var(--color-warning) 68%, var(--color-paper-2))';
    return 'color-mix(in oklch, var(--color-error) 76%, var(--color-paper-2))';
  };

  return (
    <div className="gcc-heatmap-wrap">
      <div className="gcc-heatmap-grid" role="grid" aria-label={t('24小時×7天用電熱力圖', '24x7 power load heatmap')}>
        <div className="gcc-heatmap-corner" />
        {Array.from({ length: 24 }, (_, h) => (
          <div key={h} className="gcc-heatmap-hour-label">
            {h % 3 === 0 ? `${String(h).padStart(2, '0')}` : ''}
          </div>
        ))}
        {days.map(row => (
          <div key={row.day} className="gcc-heatmap-row" role="row">
            <span className="gcc-heatmap-day-label">{dayNames[row.day]}</span>
            {row.hours.map(cell => (
              <button
                key={cell.hour}
                type="button"
                className={`gcc-heatmap-cell${cell.is_peak ? ' is-peak-hour' : ''}${selected?.day === row.day && selected?.cell.hour === cell.hour ? ' is-focused' : ''}`}
                style={{ background: colorFor(cell) }}
                aria-label={`${dayNames[row.day]} ${String(cell.hour).padStart(2, '0')}:00 — ${cell.avg_w}W`}
                onMouseEnter={() => setSelected({ day: row.day, cell })}
                onFocus={() => setSelected({ day: row.day, cell })}
              />
            ))}
          </div>
        ))}
      </div>
      <div className="gcc-heatmap-footer">
        <div className="gcc-heatmap-scale">
          <span>{t('低', 'Low')}</span>
          <i style={{ background: 'var(--color-paper)' }} />
          <i style={{ background: 'color-mix(in oklch, var(--color-accent) 26%, var(--color-paper-2))' }} />
          <i style={{ background: 'color-mix(in oklch, var(--color-accent) 56%, var(--color-paper-2))' }} />
          <i style={{ background: 'color-mix(in oklch, var(--color-warning) 68%, var(--color-paper-2))' }} />
          <i style={{ background: 'color-mix(in oklch, var(--color-error) 76%, var(--color-paper-2))' }} />
          <span>{maxW > 0 ? `${Math.round(maxW)}W` : t('高', 'High')}</span>
          <span className="gcc-heatmap-peak-key"><b />{t('虛線框＝尖峰時段', 'Dashed = Peak Hours')}</span>
        </div>
        {selected ? (
          <div className="gcc-chart-tooltip-bar">
            <strong>{dayNames[selected.day]} {String(selected.cell.hour).padStart(2, '0')}:00</strong>
            {selected.cell.samples > 0 ? (
              <>
                <span>{t('均功', 'Avg')} <b>{selected.cell.avg_w.toFixed(0)}W</b></span>
                <span><b>{formatKwh(selected.cell.kwh)}</b></span>
                <span className="is-cost">{formatMoney(selected.cell.cost, currency, 3)}</span>
                <span>{selected.cell.is_peak ? t('尖峰', 'Peak') : t('離峰', 'Off-Peak')}</span>
              </>
            ) : (
              <span>{t('此時段尚無紀錄', 'No samples recorded')}</span>
            )}
          </div>
        ) : (
          <span className="gcc-chart-hint">{t('點選或懸停格子檢視各時段平均功耗與電費', 'Hover any cell to inspect hourly power & cost')}</span>
        )}
      </div>
    </div>
  );
}

interface DonutSlice {
  label: string;
  value: number;
  color: string;
  sublabel?: string;
}

export function CostStructureDonut({
  slices,
  centerValue,
  centerLabel,
}: {
  slices: DonutSlice[];
  centerValue: string;
  centerLabel: string;
}) {
  const total = slices.reduce((s, x) => s + Math.max(0, x.value), 0);
  const R = 52;
  const C = 2 * Math.PI * R;
  let offset = 0;

  return (
    <div className="gcc-donut-layout">
      <div className="gcc-donut-ring">
        <svg viewBox="0 0 132 132" aria-hidden="true">
          <circle cx="66" cy="66" r={R} fill="none" stroke="var(--color-rule)" strokeWidth="14" />
          {total > 0 &&
            slices.map((s, idx) => {
              const frac = Math.max(0, s.value) / total;
              const dash = frac * C;
              const currentOffset = offset;
              offset += dash;
              if (dash <= 0.2) return null;
              return (
                <circle
                  key={idx}
                  cx="66"
                  cy="66"
                  r={R}
                  fill="none"
                  stroke={s.color}
                  strokeWidth="14"
                  strokeDasharray={`${dash} ${C - dash}`}
                  strokeDashoffset={-currentOffset}
                  transform="rotate(-90 66 66)"
                />
              );
            })}
        </svg>
        <div className="gcc-donut-center">
          <strong>{centerValue}</strong>
          <span>{centerLabel}</span>
        </div>
      </div>
      <ul className="gcc-donut-legend">
        {slices.map((s, idx) => {
          const pct = total > 0 ? (Math.max(0, s.value) / total) * 100 : 0;
          return (
            <li key={idx}>
              <span className="gcc-donut-dot" style={{ background: s.color }} />
              <div>
                <strong>{s.label}</strong>
                {s.sublabel && <small>{s.sublabel}</small>}
              </div>
              <span className="gcc-donut-pct">{pct.toFixed(1)}%</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function BillTrendChart({ bills, currency }: { bills: ElectricityBill[]; currency: string }) {
  const { t } = useI18n();
  const ordered = useMemo(() => [...bills].slice(0, 12).reverse(), [bills]);
  if (!ordered.length) return null;

  const W = 760;
  const H = 180;
  const pad = { top: 18, right: 16, bottom: 28, left: 56 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const maxCost = niceCeiling(Math.max(...ordered.map(b => Math.max(b.cost, b.telemetry_cost || 0)), 100) * 1.12);
  const step = plotW / ordered.length;
  const barW = Math.max(10, Math.min(34, step * 0.42));

  return (
    <div className="gcc-bill-chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="gcc-energy-svg" role="img" aria-label={t('歷史電費帳單趨勢', 'Recorded electricity bill trend')}>
        {[0, 0.5, 1].map((f, i) => {
          const val = maxCost * f;
          const y = pad.top + plotH - f * plotH;
          return (
            <g key={i}>
              <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} className="gcc-chart-grid" />
              <text x={pad.left - 8} y={y + 4} textAnchor="end" className="gcc-chart-axis-text">
                {formatMoney(val, currency, 0)}
              </text>
            </g>
          );
        })}
        {ordered.map((b, i) => {
          const cx = pad.left + (i + 0.5) * step;
          const hBill = Math.max(2, (b.cost / maxCost) * plotH);
          const hTel = b.telemetry_cost ? Math.max(2, (b.telemetry_cost / maxCost) * plotH) : 0;
          return (
            <g key={b.id}>
              <rect
                x={hTel > 0 ? cx - barW : cx - barW / 2}
                y={pad.top + plotH - hBill}
                width={barW}
                height={hBill}
                rx="3"
                fill="var(--color-info)"
                fillOpacity="0.85"
              >
                <title>{`${b.label}: ${formatMoney(b.cost, b.currency, 0)} (${b.kwh} kWh)`}</title>
              </rect>
              {hTel > 0 && (
                <rect
                  x={cx + 2}
                  y={pad.top + plotH - hTel}
                  width={Math.max(6, barW * 0.72)}
                  height={hTel}
                  rx="3"
                  fill="var(--color-accent)"
                  fillOpacity="0.9"
                >
                  <title>{`${t('GPU 遙測推估', 'GPU Telemetry Est.')}: ${formatMoney(b.telemetry_cost || 0, b.currency, 1)}`}</title>
                </rect>
              )}
              <text x={cx} y={H - 8} textAnchor="middle" className="gcc-chart-axis-text">
                {b.billing_date.slice(2, 7)}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="gcc-chart-legend" style={{ marginTop: 6 }}>
        <span><i style={{ background: 'var(--color-info)' }} />{t('實際紀錄電費', 'Recorded Bill Cost')}</span>
        <span><i style={{ background: 'var(--color-accent)' }} />{t('同期 GPU 遙測推估電費', 'Overlapping GPU Telemetry Est.')}</span>
      </div>
    </div>
  );
}

function niceCeiling(value: number): number {
  if (value <= 0 || !Number.isFinite(value)) return 10;
  const exp = Math.floor(Math.log10(value));
  const base = Math.pow(10, exp);
  const norm = value / base;
  const mult = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return mult * base;
}
