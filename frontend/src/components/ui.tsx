import { useState, type ComponentType, type ReactNode } from 'react';
import { Check, CheckCircle, CircleNotch, Clock, PauseCircle, PencilSimple, Question, WarningCircle, X, XCircle } from '@phosphor-icons/react';
import { useI18n } from '../i18n';

const statusIcons: Record<string, ComponentType<{ size?: number; weight?: 'regular' }>> = {
  completed: CheckCircle, online: CheckCircle, queued: Clock, running: CircleNotch,
  failed: WarningCircle, cancelled: XCircle, cancelling: PauseCircle,
  maintenance: PauseCircle, unknown: Question, offline: XCircle,
};

export function Badge({ value }: { value: string }) {
  const { statusLabel } = useI18n();
  const Icon = statusIcons[value] || Question;
  return <span className={`gcc-badge gcc-badge--${value}`}><Icon size={14} weight="regular" />{statusLabel(value)}</span>;
}

export function UsageBar({ value, label, warning = false }: { value: number | null; label: string; warning?: boolean }) {
  const { t } = useI18n();
  const valid = value !== null && Number.isFinite(value);
  const amount = valid ? Math.max(0, Math.min(100, value)) : 0;
  return <div className={`gcc-meter${warning ? ' gcc-meter--warning' : ''}`} role="progressbar" aria-label={label} aria-valuenow={valid ? Math.round(amount) : undefined} aria-valuemin={0} aria-valuemax={100} aria-valuetext={valid ? `${amount.toFixed(0)}%` : t('無資料', 'No data')}><span style={{ transform: `scaleX(${amount / 100})` }} /></div>;
}

export function Empty({ icon, title, children, action }: { icon?: ReactNode; title: string; children: ReactNode; action?: ReactNode }) {
  return <div className="gcc-empty">{icon && <span className="gcc-empty-icon" aria-hidden="true">{icon}</span>}<h3>{title}</h3><p>{children}</p>{action}</div>;
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  const { t } = useI18n();
  return <div className="gcc-skeleton" role="status" aria-label={t('正在載入資料', 'Loading data')}>{Array.from({ length: rows }, (_, i) => <div className="gcc-skeleton-row" key={i}><span /><span /><span /></div>)}</div>;
}

export function EditableModelName({
  displayName,
  rawName,
  disabled = false,
  onSave,
}: {
  displayName: string;
  rawName: string;
  disabled?: boolean;
  onSave: (alias: string) => void;
}) {
  const { t } = useI18n();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(displayName);
  const hasAlias = displayName !== rawName;

  const commit = () => {
    const trimmed = draft.trim();
    const next = !trimmed || trimmed === rawName ? '' : trimmed;
    setEditing(false);
    if ((next || rawName) !== displayName) {
      onSave(next);
    }
  };

  if (editing) {
    return <form className="gcc-rename-form" onSubmit={e => { e.preventDefault(); commit(); }}>
      <input
        autoFocus
        value={draft}
        maxLength={100}
        placeholder={rawName}
        aria-label={t('模型顯示名稱', 'Model display name')}
        onChange={e => setDraft(e.target.value)}
        onKeyDown={e => { if (e.key === 'Escape') { e.preventDefault(); setEditing(false); } }}
      />
      <button type="submit" className="gcc-icon-button gcc-rename-confirm" title={t('儲存名稱（留空可還原預設）', 'Save name (leave blank to reset)')} aria-label={t('儲存名稱', 'Save name')}><Check size={14}/></button>
      <button type="button" className="gcc-icon-button" title={t('取消', 'Cancel')} aria-label={t('取消編輯', 'Cancel edit')} onClick={() => setEditing(false)}><X size={14}/></button>
    </form>;
  }

  return <span className="gcc-editable-name">
    <span className="gcc-editable-name-text">
      <strong>{displayName}</strong>
      {hasAlias && <code className="gcc-raw-model-id">{rawName}</code>}
    </span>
    <button
      type="button"
      className="gcc-rename-btn"
      disabled={disabled}
      title={t('編輯顯示名稱', 'Edit display name')}
      aria-label={t(`編輯 ${displayName} 名稱`, `Edit name for ${displayName}`)}
      onClick={() => { setDraft(displayName); setEditing(true); }}
    ><PencilSimple size={13}/></button>
  </span>;
}
