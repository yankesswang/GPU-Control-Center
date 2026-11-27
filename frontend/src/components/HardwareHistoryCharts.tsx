import { useState } from 'react';
import {
  type HardwareGpuSeriesMeta,
  type HardwareThermalCurveBin,
  type HardwareTimeseriesPoint,
} from '../api';
import { useI18n } from '../i18n';

const GPU_COLORS = [
  'var(--color-accent)',
  'var(--color-info)',
  'var(--color-purple)',
  'var(--color-warning)',
  'var(--color-error)',
  'var(--color-ink-2)',
];

export type TelemetryChartMode = 'thermal' | 'compute' | 'system';

interface TelemetryChartProps {
  points: HardwareTimeseriesPoint[];
  gpuMeta: HardwareGpuSeriesMeta[];
  mode: TelemetryChartMode;
  tempWarnC: number;
  tempCritC: number;
}

export function HardwareTelemetryChart({
  points,
  gpuMeta,
  mode,
  tempWarnC,
  tempCritC,
}: TelemetryChartProps) {
  const { t } = useI18n();
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  const W = 820;
  const H = 268;
  const pad = { top: 20, right: 20, bottom: 34, left: 52 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;

  if (!points.length) {
    return (
      <div className="gcc-chart-empty">
        <p>
          {t(
            '目前區間尚無硬體歷史遙測紀錄，主機 Agent 連線後每 4 秒自動寫入。',
            'No hardware history samples in this range yet. Samples are recorded every 4 seconds once an agent connects.',
          )}
        </p>
      </div>
    );
  }

  const n = points.length;
  const maxY = mode === 'thermal' ? 100 : 100;
  const ticks = mode === 'thermal' ? [0, 25, 50, 75, 100] : [0, 25, 50, 75, 100];
  const xFor = (i: number) => (n === 1 ? pad.left + plotW / 2 : pad.left + (i / (n - 1)) * plotW);
  const yFor = (val: number) => pad.top + plotH - Math.min(1, Math.max(0, val / maxY)) * plotH;

  const buildPath = (getter: (p: HardwareTimeseriesPoint) => number | null | undefined) => {
    const segs: string[] = [];
    let started = false;
    points.forEach((p, i) => {
      const v = getter(p);
      if (v == null || !Number.isFinite(v)) {
        started = false;
        return;
      }
      segs.push(`${started ? 'L' : 'M'}${xFor(i).toFixed(1)},${yFor(v).toFixed(1)}`);
      started = true;
    });
    return segs.join(' ');
  };

  const labelStride = Math.max(1, Math.ceil(n / 6));
  const step = plotW / Math.max(1, n);
  const hovered = hoverIdx !== null && points[hoverIdx] ? points[hoverIdx] : null;
  const hoveredX = hoverIdx !== null ? xFor(hoverIdx) : 0;

  return (
    <div className="gcc-chart-wrap">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="gcc-energy-svg"
        role="img"
        aria-label={t('硬體歷史遙測趨勢圖', 'Hardware telemetry time series chart')}
        onMouseLeave={() => setHoverIdx(null)}
      >
        {/* Gridlines & Y labels */}
        {ticks.map(tv => {
          const y = yFor(tv);
          return (
            <g key={tv}>
              <line x1={pad.left} x2={W - pad.right} y1={y} y2={y} className="gcc-chart-grid" />
              <text x={pad.left - 8} y={y + 4} textAnchor="end" className="gcc-chart-axis-text">
                {mode === 'thermal' ? `${tv}°C` : `${tv}%`}
              </text>
            </g>
          );
        })}

        {/* Thermal mode: per-GPU temperature curves + fan % + thresholds */}
        {mode === 'thermal' && (
          <>
            <line x1={pad.left} x2={W - pad.right} y1={yFor(tempCritC)} y2={yFor(tempCritC)} stroke="var(--color-error)" strokeWidth="1" strokeDasharray="4 4" opacity="0.7" />
            <text x={W - pad.right - 4} y={yFor(tempCritC) - 4} textAnchor="end" className="gcc-chart-ref-label" style={{ fill: 'var(--color-error)' }}>
              CRIT {tempCritC}°C
            </text>
            <line x1={pad.left} x2={W - pad.right} y1={yFor(tempWarnC)} y2={yFor(tempWarnC)} stroke="var(--color-warning)" strokeWidth="1" strokeDasharray="3 4" opacity="0.7" />
            <text x={W - pad.right - 4} y={yFor(tempWarnC) - 4} textAnchor="end" className="gcc-chart-ref-label is-warn">
              WARN {tempWarnC}°C
            </text>

            {/* Avg fan speed dashed line */}
            {n > 1 && (
              <path
                d={buildPath(p => p.avg_fan_pct)}
                fill="none"
                stroke="var(--color-muted)"
                strokeWidth="1.4"
                strokeDasharray="3 3"
                opacity="0.75"
              />
            )}

            {/* Multi-GPU lines (or aggregate temp if single/no meta) */}
            {gpuMeta.length > 0 ? (
              gpuMeta.map((gm, idx) => {
                const color = GPU_COLORS[idx % GPU_COLORS.length];
                const d = buildPath(p => p.per_gpu_temp[gm.key]);
                if (!d) return null;
                return n > 1 ? (
                  <path key={gm.key} d={d} fill="none" stroke={color} strokeWidth="2.2" strokeLinejoin="round" />
                ) : (
                  <circle
                    key={gm.key}
                    cx={xFor(0)}
                    cy={yFor(points[0].per_gpu_temp[gm.key] ?? 0)}
                    r="5"
                    fill={color}
                  />
                );
              })
            ) : (
              n > 1 ? (
                <path d={buildPath(p => p.avg_temp_c)} fill="none" stroke="var(--color-warning)" strokeWidth="2.2" />
              ) : (
                points[0].avg_temp_c != null && <circle cx={xFor(0)} cy={yFor(points[0].avg_temp_c)} r="5" fill="var(--color-warning)" />
              )
            )}
          </>
        )}

        {/* Compute mode: GPU Utilization % & VRAM % */}
        {mode === 'compute' && (
          <>
            <line x1={pad.left} x2={W - pad.right} y1={yFor(88)} y2={yFor(88)} stroke="var(--color-warning)" strokeWidth="1" strokeDasharray="3 4" opacity="0.65" />
            <text x={W - pad.right - 4} y={yFor(88) - 4} textAnchor="end" className="gcc-chart-ref-label is-warn">
              VRAM WARN 88%
            </text>
            {n > 1 ? (
              <>
                <path d={buildPath(p => p.avg_vram_pct)} fill="none" stroke="var(--color-info)" strokeWidth="2.2" strokeLinejoin="round" />
                <path d={buildPath(p => p.avg_gpu_util)} fill="none" stroke="var(--color-accent)" strokeWidth="2.2" strokeLinejoin="round" />
                <path d={buildPath(p => p.max_gpu_util)} fill="none" stroke="var(--color-accent)" strokeWidth="1.2" strokeDasharray="3 3" opacity="0.6" />
              </>
            ) : (
              <>
                {points[0].avg_vram_pct != null && <circle cx={xFor(0)} cy={yFor(points[0].avg_vram_pct)} r="5" fill="var(--color-info)" />}
                {points[0].avg_gpu_util != null && <circle cx={xFor(0)} cy={yFor(points[0].avg_gpu_util)} r="5" fill="var(--color-accent)" />}
              </>
            )}
          </>
        )}

        {/* System mode: Host CPU % & RAM % */}
        {mode === 'system' && (
          <>
            {n > 1 ? (
              <>
                <path d={buildPath(p => p.avg_ram_pct)} fill="none" stroke="var(--color-purple)" strokeWidth="2.2" strokeLinejoin="round" />
                <path d={buildPath(p => p.avg_cpu_pct)} fill="none" stroke="var(--color-info)" strokeWidth="2.2" strokeLinejoin="round" />
              </>
            ) : (
              <>
                {points[0].avg_ram_pct != null && <circle cx={xFor(0)} cy={yFor(points[0].avg_ram_pct)} r="5" fill="var(--color-purple)" />}
                {points[0].avg_cpu_pct != null && <circle cx={xFor(0)} cy={yFor(points[0].avg_cpu_pct)} r="5" fill="var(--color-info)" />}
              </>
            )}
          </>
        )}

        {/* X axis labels */}
        {points.map((p, i) => {
          if (i % labelStride !== 0 && i !== n - 1) return null;
          return (
            <text key={`x-${i}`} x={xFor(i)} y={H - 10} textAnchor="middle" className="gcc-chart-axis-text">
              {p.label}
            </text>
          );
        })}

        {/* Hover crosshair */}
        {hovered && hoverIdx !== null && (
          <line
            x1={hoveredX}
            x2={hoveredX}
            y1={pad.top}
            y2={pad.top + plotH}
            stroke="var(--color-ink-2)"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.65"
          />
        )}

        {/* Hover hit zones */}
        {points.map((_, i) => (
          <rect
            key={`hit-${i}`}
            x={n === 1 ? pad.left : Math.max(pad.left, xFor(i) - step / 2)}
            y={pad.top}
            width={n === 1 ? plotW : Math.max(4, step)}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setHoverIdx(i)}
          />
        ))}
      </svg>

      <div className="gcc-chart-footer">
        <div className="gcc-chart-legend">
          {mode === 'thermal' && (
            <>
              {gpuMeta.map((gm, idx) => (
                <span key={gm.key}>
                  <i style={{ background: GPU_COLORS[idx % GPU_COLORS.length] }} />
                  {gm.label} (°C)
                </span>
              ))}
              <span><i style={{ background: 'var(--color-muted)' }} />{t('平均風扇轉速 (%)', 'Avg Fan (%)')}</span>
            </>
          )}
          {mode === 'compute' && (
            <>
              <span><i style={{ background: 'var(--color-accent)' }} />{t('GPU 平均負載 (%)', 'Avg GPU Util (%)')}</span>
              <span><i style={{ background: 'var(--color-info)' }} />{t('VRAM 顯存占用 (%)', 'VRAM Used (%)')}</span>
            </>
          )}
          {mode === 'system' && (
            <>
              <span><i style={{ background: 'var(--color-info)' }} />{t('主機 CPU (%)', 'Host CPU (%)')}</span>
              <span><i style={{ background: 'var(--color-purple)' }} />{t('系統 RAM (%)', 'Host RAM (%)')}</span>
            </>
          )}
        </div>

        {hovered ? (
          <div className="gcc-chart-tooltip-bar">
            <strong>{hovered.label}</strong>
            {mode === 'thermal' && (
              <>
                {gpuMeta.map(gm =>
                  hovered.per_gpu_temp[gm.key] != null ? (
                    <span key={gm.key}>{gm.label} <b>{hovered.per_gpu_temp[gm.key].toFixed(0)}°C</b></span>
                  ) : null,
                )}
                {hovered.avg_fan_pct != null && <span>{t('風扇', 'Fan')} <b>{hovered.avg_fan_pct.toFixed(0)}%</b></span>}
                {hovered.avg_clock_mhz != null && <span><b>{Math.round(hovered.avg_clock_mhz)} MHz</b></span>}
              </>
            )}
            {mode === 'compute' && (
              <>
                {hovered.avg_gpu_util != null && <span>GPU <b>{hovered.avg_gpu_util.toFixed(0)}%</b> ({t('峰', 'max')} {hovered.max_gpu_util?.toFixed(0)}%)</span>}
                {hovered.avg_vram_pct != null && <span>VRAM <b>{hovered.avg_vram_pct.toFixed(1)}%</b> ({hovered.avg_vram_gb?.toFixed(1)} GiB)</span>}
                {hovered.avg_power_w != null && <span><b>{hovered.avg_power_w.toFixed(0)}W</b></span>}
              </>
            )}
            {mode === 'system' && (
              <>
                {hovered.avg_cpu_pct != null && <span>CPU <b>{hovered.avg_cpu_pct.toFixed(1)}%</b></span>}
                {hovered.avg_ram_pct != null && <span>RAM <b>{hovered.avg_ram_pct.toFixed(1)}%</b></span>}
                <span>{hovered.samples} {t('樣本', 'samples')}</span>
              </>
            )}
          </div>
        ) : (
          <span className="gcc-chart-hint">{t('滑鼠移至圖表檢視各時段硬體細節', 'Hover chart to inspect bucket vitals')}</span>
        )}
      </div>
    </div>
  );
}

export function ThermalLoadCurveChart({ bins }: { bins: HardwareThermalCurveBin[] }) {
  const { t } = useI18n();
  const hasData = bins.some(b => b.samples > 0);
  if (!hasData) {
    return (
      <div className="gcc-chart-empty">
        <p>{t('尚無足夠負載樣本建立散熱曲線。', 'Not enough load samples yet to plot the thermal response curve.')}</p>
      </div>
    );
  }

  const W = 480;
  const H = 200;
  const pad = { top: 18, right: 16, bottom: 28, left: 42 };
  const plotW = W - pad.left - pad.right;
  const plotH = H - pad.top - pad.bottom;
  const step = plotW / bins.length;
  const barW = Math.max(12, step * 0.54);
  const yFor = (val: number) => pad.top + plotH - Math.min(1, Math.max(0, val / 100)) * plotH;

  const fanPoints = bins
    .map((b, i) => (b.avg_fan_pct != null ? `${pad.left + (i + 0.5) * step},${yFor(b.avg_fan_pct)}` : null))
    .filter(Boolean) as string[];

  return (
    <div className="gcc-chart-wrap">
      <svg viewBox={`0 0 ${W} ${H}`} className="gcc-energy-svg" role="img" aria-label={t('負載與溫度散熱曲線', 'Load vs Temperature thermal curve')}>
        {[0, 25, 50, 75, 100].map(tv => (
          <g key={tv}>
            <line x1={pad.left} x2={W - pad.right} y1={yFor(tv)} y2={yFor(tv)} className="gcc-chart-grid" />
            <text x={pad.left - 6} y={yFor(tv) + 3} textAnchor="end" className="gcc-chart-axis-text">
              {tv}°
            </text>
          </g>
        ))}

        {bins.map((b, i) => {
          const cx = pad.left + (i + 0.5) * step;
          const temp = b.avg_temp_c;
          const fill =
            temp == null
              ? 'var(--color-rule)'
              : temp >= 75
                ? 'var(--color-error)'
                : temp >= 60
                  ? 'var(--color-warning)'
                  : 'var(--color-accent)';
          const h = temp != null ? Math.max(3, pad.top + plotH - yFor(temp)) : 0;
          return (
            <g key={b.bin_start}>
              {temp != null && (
                <>
                  <rect x={cx - barW / 2} y={yFor(temp)} width={barW} height={h} rx="3" fill={fill} fillOpacity="0.82">
                    <title>{`${b.label}: ${t('均溫', 'Avg')} ${temp}°C, ${t('最高', 'Max')} ${b.max_temp_c}°C (${b.samples} samples)`}</title>
                  </rect>
                  <text x={cx} y={yFor(temp) - 4} textAnchor="middle" className="gcc-chart-axis-text" style={{ fontSize: 9 }}>
                    {Math.round(temp)}°
                  </text>
                </>
              )}
              <text x={cx} y={H - 8} textAnchor="middle" className="gcc-chart-axis-text" style={{ fontSize: 9 }}>
                {b.bin_start}%
              </text>
            </g>
          );
        })}

        {fanPoints.length > 1 && (
          <polyline
            points={fanPoints.join(' ')}
            fill="none"
            stroke="var(--color-info)"
            strokeWidth="2"
            strokeDasharray="4 2"
          />
        )}
      </svg>
      <div className="gcc-chart-legend">
        <span><i style={{ background: 'var(--color-accent)' }} />{'< 60°C'}</span>
        <span><i style={{ background: 'var(--color-warning)' }} />60–75°C</span>
        <span><i style={{ background: 'var(--color-error)' }} />{'≥ 75°C'}</span>
        <span><i style={{ background: 'var(--color-info)' }} />{t('風扇轉速曲線 (%)', 'Fan Curve (%)')}</span>
      </div>
    </div>
  );
}
