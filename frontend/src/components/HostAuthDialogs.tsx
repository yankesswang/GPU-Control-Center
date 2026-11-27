import React, { useEffect, useRef, useState } from 'react';
import {
  CircleNotch,
  Eye,
  EyeSlash,
  Key,
  LockSimple,
  LockSimpleOpen,
  ShieldCheck,
  WarningCircle,
  X,
} from '@phosphor-icons/react';
import { api, type Node } from '../api';
import { useI18n } from '../i18n';

function remainingMinutes(expiresAt?: string | null): number | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - Date.now();
  if (!Number.isFinite(ms) || ms <= 0) return null;
  return Math.max(1, Math.ceil(ms / 60000));
}

export function HostAuthControl({
  node,
  busy,
  onUnlock,
  onLock,
  onManagePassword,
}: {
  node: Node;
  busy: boolean;
  onUnlock: (node: Node) => void;
  onLock: (node: Node) => void;
  onManagePassword: (node: Node) => void;
}) {
  const { t } = useI18n();
  if (node.mode === 'demo') return null;

  if (!node.auth_required) {
    return (
      <button
        type="button"
        className="gcc-button gcc-button--quiet gcc-btn-sm gcc-auth-btn"
        disabled={busy}
        title={t('設定此主機的模型操作密碼', 'Set an operation password for model controls on this host')}
        onClick={() => onManagePassword(node)}
      >
        <ShieldCheck size={14} />
        {t('設定密碼', 'Set Password')}
      </button>
    );
  }

  if (!node.auth_unlocked) {
    return (
      <div className="gcc-host-auth-group">
        <button
          type="button"
          className="gcc-button gcc-btn-sm gcc-auth-btn is-locked"
          disabled={busy}
          title={t('驗證密碼以解鎖此主機的模型載入與卸載操作', 'Verify password to unlock model load/unload on this host')}
          onClick={() => onUnlock(node)}
        >
          <LockSimple size={14} />
          {t('已鎖定 · 解鎖', 'Locked · Unlock')}
        </button>
        <button
          type="button"
          className="gcc-icon-button gcc-auth-settings-btn"
          disabled={busy}
          aria-label={t(`管理 ${node.name} 操作密碼`, `Manage ${node.name} password`)}
          title={t('變更或移除密碼', 'Change or remove password')}
          onClick={() => onManagePassword(node)}
        >
          <Key size={15} />
        </button>
      </div>
    );
  }

  const mins = remainingMinutes(node.auth_unlock_expires_at);
  return (
    <div className="gcc-host-auth-group">
      <button
        type="button"
        className="gcc-button gcc-btn-sm gcc-auth-btn is-unlocked"
        disabled={busy}
        title={t('點擊立即鎖定此主機', 'Click to lock this host immediately')}
        onClick={() => onLock(node)}
      >
        <LockSimpleOpen size={14} />
        <span>{mins != null ? t(`已解鎖 · ${mins} 分`, `Unlocked · ${mins}m`) : t('已解鎖', 'Unlocked')}</span>
        <span className="gcc-auth-lock-verb">{t('立即鎖定', 'Lock')}</span>
      </button>
      <button
        type="button"
        className="gcc-icon-button gcc-auth-settings-btn"
        disabled={busy}
        aria-label={t(`管理 ${node.name} 操作密碼`, `Manage ${node.name} password`)}
        title={t('變更或移除密碼', 'Change or remove password')}
        onClick={() => onManagePassword(node)}
      >
        <Key size={15} />
      </button>
    </div>
  );
}

export function HostUnlockDialog({
  node,
  pendingLabel,
  onClose,
  onUnlocked,
}: {
  node: Node;
  pendingLabel?: string | null;
  onClose: () => void;
  onUnlocked: () => Promise<void> | void;
}) {
  const { t, translateApiText } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!password) return;
    setSubmitting(true);
    setError('');
    try {
      await api(`/nodes/${node.id}/auth/unlock`, 'POST', { password });
      await onUnlocked();
    } catch (err) {
      setError(err instanceof Error ? translateApiText(err.message) : t('密碼驗證失敗', 'Password verification failed'));
      setSubmitting(false);
      setPassword('');
      setTimeout(() => inputRef.current?.focus(), 0);
    }
  }

  return (
    <dialog
      ref={ref}
      className="gcc-dialog gcc-create-dialog gcc-host-auth-dialog"
      aria-labelledby="host-unlock-title"
      onCancel={e => {
        if (submitting) e.preventDefault();
        else onClose();
      }}
    >
      <form onSubmit={e => void submit(e)}>
        <div className="gcc-dialog-head">
          <span>
            <LockSimple size={18} />
            {t('主機操作驗證', 'Host Verification')}
          </span>
          <button
            type="button"
            className="gcc-icon-button"
            aria-label={t('關閉驗證視窗', 'Close verification dialog')}
            onClick={onClose}
            disabled={submitting}
          >
            <X size={21} />
          </button>
        </div>
        <div className="gcc-create-body">
          <h2 id="host-unlock-title">{t(`解鎖 ${node.name}`, `Unlock ${node.name}`)}</h2>
          <p className="gcc-create-description">
            {t(
              '請輸入此主機的操作密碼以執行模型載入、卸載與 GPU 切換。驗證成功後將解鎖 30 分鐘，可隨時手動鎖定。',
              'Enter this host operation password to load, unload, or move models across GPUs. Remains unlocked for 30 minutes and can be locked anytime.',
            )}
          </p>
          {pendingLabel && (
            <div className="gcc-auth-pending-callout" role="status">
              <LockSimpleOpen size={16} />
              <span>
                {t('驗證後將繼續執行：', 'Will continue after unlock: ')}
                <strong>{pendingLabel}</strong>
              </span>
            </div>
          )}
          <label className="gcc-field">
            <span>{t('操作密碼', 'Operation Password')}</span>
            <div className="gcc-password-wrap">
              <input
                ref={inputRef}
                type={showPassword ? 'text' : 'password'}
                aria-label={t('操作密碼', 'Operation Password')}
                autoFocus
                autoComplete="current-password"
                value={password}
                onChange={e => {
                  setPassword(e.target.value);
                  if (error) setError('');
                }}
                maxLength={128}
                disabled={submitting}
                placeholder={t('請輸入主機操作密碼', 'Enter host password')}
              />
              <button
                type="button"
                className="gcc-icon-button gcc-password-toggle"
                aria-label={showPassword ? t('隱藏密碼', 'Hide password') : t('顯示密碼', 'Show password')}
                onClick={() => setShowPassword(v => !v)}
                disabled={submitting}
              >
                {showPassword ? <EyeSlash size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>
          {error && (
            <div className="gcc-alert" role="alert">
              <WarningCircle size={18} />
              <p>{error}</p>
            </div>
          )}
        </div>
        <div className="gcc-create-actions">
          <span className="gcc-muted">{t('解鎖狀態僅限本瀏覽器工作階段', 'Unlock applies to this browser session')}</span>
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={submitting}>
              {t('取消', 'Cancel')}
            </button>
            <button
              type="submit"
              className="gcc-button gcc-button--primary"
              disabled={!password || submitting}
            >
              {submitting ? <CircleNotch size={17} className="gcc-spin" /> : <LockSimpleOpen size={17} />}
              {submitting ? t('驗證中', 'Verifying…') : pendingLabel ? t('驗證並執行', 'Verify & Run') : t('解鎖 30 分鐘', 'Unlock for 30m')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}

export function HostPasswordSettingsDialog({
  node,
  onClose,
  onUpdated,
}: {
  node: Node;
  onClose: () => void;
  onUpdated: () => Promise<void>;
}) {
  const { t, translateApiText } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [showPasswords, setShowPasswords] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  const hasExisting = Boolean(node.auth_required);
  const validNew = newPassword.length >= 4 && newPassword.trim().length > 0 && newPassword.length <= 128;
  const confirmMatches = confirmPassword === newPassword;
  const canSaveNew = (!hasExisting || currentPassword.length > 0) && validNew && confirmMatches && !saving;
  const canRemove = hasExisting && currentPassword.length > 0 && !saving;

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  async function saveNewPassword(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!canSaveNew) return;
    setSaving(true);
    setError('');
    try {
      await api<Node>(`/nodes/${node.id}/auth/password`, 'PATCH', {
        current_password: hasExisting ? currentPassword : null,
        new_password: newPassword,
      });
      await onUpdated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? translateApiText(err.message) : t('密碼更新失敗', 'Failed to update password'));
      setSaving(false);
    }
  }

  async function removePassword() {
    if (!canRemove) return;
    setSaving(true);
    setError('');
    try {
      await api<Node>(`/nodes/${node.id}/auth/password`, 'PATCH', {
        current_password: currentPassword,
        new_password: '',
      });
      await onUpdated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? translateApiText(err.message) : t('移除密碼失敗', 'Failed to remove password'));
      setSaving(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="gcc-dialog gcc-create-dialog gcc-host-auth-dialog"
      aria-labelledby="host-password-title"
      onCancel={e => {
        if (saving) e.preventDefault();
        else onClose();
      }}
    >
      <form onSubmit={e => void saveNewPassword(e)}>
        <div className="gcc-dialog-head">
          <span>
            <Key size={18} />
            {t('主機操作密碼設定', 'Host Operation Password')}
          </span>
          <button
            type="button"
            className="gcc-icon-button"
            aria-label={t('關閉密碼設定', 'Close password settings')}
            onClick={onClose}
            disabled={saving}
          >
            <X size={21} />
          </button>
        </div>
        <div className="gcc-create-body">
          <h2 id="host-password-title">
            {hasExisting
              ? t(`管理 ${node.name} 的操作密碼`, `Manage Password for ${node.name}`)
              : t(`為 ${node.name} 設定操作密碼`, `Set Operation Password for ${node.name}`)}
          </h2>
          <p className="gcc-create-description">
            {hasExisting
              ? t(
                  '變更或移除密碼須先驗證目前密碼。更新後，所有既有的解鎖狀態將立即失效。',
                  'Enter the current password to update or remove protection. Updating invalidates all active unlock sessions.',
                )
              : t(
                  '啟用後，在此主機執行模型載入、卸載與 GPU 切換前須驗證密碼；一般推論與硬體監測不受影響。',
                  'Once enabled, loading, unloading, or moving models on this host requires password verification. Regular inference and monitoring remain open.',
                )}
          </p>

          {hasExisting && (
            <label className="gcc-field">
              <span>
                {t('目前密碼', 'Current Password')} <small>{t('必填', 'Required')}</small>
              </span>
              <div className="gcc-password-wrap">
                <input
                  type={showPasswords ? 'text' : 'password'}
                  aria-label={t('目前密碼', 'Current Password')}
                  autoFocus
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={e => {
                    setCurrentPassword(e.target.value);
                    if (error) setError('');
                  }}
                  maxLength={128}
                  disabled={saving}
                  placeholder={t('輸入目前操作密碼', 'Enter current password')}
                />
                <button
                  type="button"
                  className="gcc-icon-button gcc-password-toggle"
                  aria-label={showPasswords ? t('隱藏密碼', 'Hide passwords') : t('顯示密碼', 'Show passwords')}
                  onClick={() => setShowPasswords(v => !v)}
                  disabled={saving}
                >
                  {showPasswords ? <EyeSlash size={17} /> : <Eye size={17} />}
                </button>
              </div>
            </label>
          )}

          <label className="gcc-field">
            <span>{hasExisting ? t('新操作密碼', 'New Password') : t('操作密碼', 'Operation Password')}</span>
            <div className="gcc-password-wrap">
              <input
                type={showPasswords ? 'text' : 'password'}
                aria-label={hasExisting ? t('新操作密碼', 'New Password') : t('操作密碼', 'Operation Password')}
                autoFocus={!hasExisting}
                autoComplete="new-password"
                value={newPassword}
                onChange={e => {
                  setNewPassword(e.target.value);
                  if (error) setError('');
                }}
                maxLength={128}
                disabled={saving}
                aria-invalid={!!newPassword && !validNew}
                placeholder={t('至少 4 個字元', 'At least 4 characters')}
              />
              {!hasExisting && (
                <button
                  type="button"
                  className="gcc-icon-button gcc-password-toggle"
                  aria-label={showPasswords ? t('隱藏密碼', 'Hide passwords') : t('顯示密碼', 'Show passwords')}
                  onClick={() => setShowPasswords(v => !v)}
                  disabled={saving}
                >
                  {showPasswords ? <EyeSlash size={17} /> : <Eye size={17} />}
                </button>
              )}
            </div>
            <span className={`gcc-field-help${newPassword && !validNew ? ' is-error' : ''}`}>
              {newPassword && !validNew
                ? t('密碼長度需至少 4 個字元，且不可全為空白。', 'Password must be at least 4 non-blank characters.')
                : t('長度 4–128 個字元。', '4–128 characters.')}
            </span>
          </label>

          <label className="gcc-field">
            <span>{t('確認密碼', 'Confirm Password')}</span>
            <input
              type={showPasswords ? 'text' : 'password'}
              aria-label={t('確認密碼', 'Confirm Password')}
              autoComplete="new-password"
              value={confirmPassword}
              onChange={e => setConfirmPassword(e.target.value)}
              maxLength={128}
              disabled={saving}
              aria-invalid={!!confirmPassword && !confirmMatches}
              placeholder={t('再次輸入操作密碼', 'Re-enter password')}
            />
            {confirmPassword && !confirmMatches && (
              <span className="gcc-field-help is-error">
                {t('兩次輸入的密碼不一致。', 'Passwords do not match.')}
              </span>
            )}
          </label>

          {error && (
            <div className="gcc-alert" role="alert">
              <WarningCircle size={18} />
              <p>{error}</p>
            </div>
          )}
        </div>
        <div className="gcc-create-actions">
          {hasExisting ? (
            <button
              type="button"
              className="gcc-button gcc-button--danger"
              disabled={!canRemove}
              onClick={() => void removePassword()}
            >
              {t('移除密碼保護', 'Remove Password')}
            </button>
          ) : (
            <span className="gcc-muted">{t('密碼以 scrypt 雜湊儲存', 'Stored with salted scrypt hash')}</span>
          )}
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={saving}>
              {t('取消', 'Cancel')}
            </button>
            <button type="submit" className="gcc-button gcc-button--primary" disabled={!canSaveNew}>
              {saving ? <CircleNotch size={17} className="gcc-spin" /> : <ShieldCheck size={17} />}
              {saving
                ? t('儲存中', 'Saving…')
                : hasExisting
                  ? t('更新密碼', 'Update Password')
                  : t('啟用密碼保護', 'Enable Protection')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
