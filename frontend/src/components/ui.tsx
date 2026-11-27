import type { ComponentType, ReactNode } from 'react';
import { CheckCircle, CircleNotch, Clock, PauseCircle, Question, WarningCircle, XCircle } from '@phosphor-icons/react';

export const statusLabels: Record<string, string> = {
  queued: '排隊中', running: '執行中', completed: '已完成', failed: '失敗',
  cancelled: '已取消', cancelling: '取消中', unknown: '待確認',
  online: '在線', offline: '離線', maintenance: '維護中',
};
export const time = (value: string) => new Date(value).toLocaleString('zh-TW', { hour12: false });
export const clock = (value: string) => new Date(value).toLocaleTimeString('zh-TW', { hour12: false });

const statusIcons: Record<string, ComponentType<{ size?: number; weight?: 'regular' }>> = {
  completed: CheckCircle, online: CheckCircle, queued: Clock, running: CircleNotch,
  failed: WarningCircle, cancelled: XCircle, cancelling: PauseCircle,
  maintenance: PauseCircle, unknown: Question, offline: XCircle,
};

export function Badge({ value }: { value: string }) {
  const Icon = statusIcons[value] || Question;
  return <span className={`gcc-badge gcc-badge--${value}`}><Icon size={14} weight="regular" />{statusLabels[value] || value}</span>;
}

export function UsageBar({ value, label, warning = false }: { value: number | null; label: string; warning?: boolean }) {
  const valid = value !== null && Number.isFinite(value);
  const amount = valid ? Math.max(0, Math.min(100, value)) : 0;
  return <div className={`gcc-meter${warning ? ' gcc-meter--warning' : ''}`} role="progressbar" aria-label={label} aria-valuenow={valid ? Math.round(amount) : undefined} aria-valuemin={0} aria-valuemax={100} aria-valuetext={valid ? `${amount.toFixed(0)}%` : '無資料'}><span style={{ transform: `scaleX(${amount / 100})` }} /></div>;
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="gcc-empty">{icon && <span className="gcc-empty-icon" aria-hidden="true">{icon}</span>}<h3>{title}</h3><p>{children}</p>{action}</div>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return <div className="gcc-skeleton" role="status" aria-label="正在載入資料">{Array.from({ length: rows }, (_, i) => <div className="gcc-skeleton-row" key={i}><span /><span /><span /></div>)}</div>;
}
