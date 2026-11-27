import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

export type Locale = 'zh-TW' | 'en';

const STORAGE_KEY = 'gcc.locale';

function readInitialLocale(): Locale {
  if (typeof window === 'undefined') return 'zh-TW';
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved === 'en' || saved === 'zh-TW') return saved;
  } catch {
    // Ignore storage access errors.
  }
  return 'zh-TW';
}

export interface I18nContextValue {
  locale: Locale;
  isZh: boolean;
  setLocale: (locale: Locale) => void;
  toggleLocale: () => void;
  t: (zh: string, en: string) => string;
  formatTime: (value: string) => string;
  formatClock: (value: string) => string;
  formatNowClock: () => string;
  statusLabel: (status: string) => string;
  blockerLabel: (blocker: string) => string;
  comfyRoleLabel: (role: string) => string;
  processCategoryLabel: (category: string) => string;
  translateApiText: (text: string) => string;
}

const STATUS_LABELS: Record<Locale, Record<string, string>> = {
  'zh-TW': {
    queued: '排隊中',
    running: '執行中',
    completed: '已完成',
    failed: '失敗',
    cancelled: '已取消',
    cancelling: '取消中',
    unknown: '待確認',
    online: '在線',
    offline: '離線',
    maintenance: '維護中',
  },
  en: {
    queued: 'Queued',
    running: 'Running',
    completed: 'Completed',
    failed: 'Failed',
    cancelled: 'Cancelled',
    cancelling: 'Cancelling',
    unknown: 'Unconfirmed',
    online: 'Online',
    offline: 'Offline',
    maintenance: 'Maintenance',
  },
};

const BLOCKER_LABELS: Record<Locale, Record<string, string>> = {
  'zh-TW': {
    unsupported_capability: '不支援此能力',
    offline: '主機離線',
    maintenance: '維護中',
    unconfirmed_execution: '有執行狀態待確認的工作',
    slot_occupied: '工作槽已占用',
    different_target_node: '指定其他主機',
    thermal_guard: 'GPU 溫度保護暫緩',
    peak_tariff_deferred: '尖峰電價自動暫緩',
    no_compatible_node: '無相容主機',
  },
  en: {
    unsupported_capability: 'Unsupported capability',
    offline: 'Host offline',
    maintenance: 'In maintenance',
    unconfirmed_execution: 'Unconfirmed execution pending',
    slot_occupied: 'Execution slot occupied',
    different_target_node: 'Targeted to another host',
    thermal_guard: 'Deferred by GPU thermal guard',
    peak_tariff_deferred: 'Deferred during peak tariff',
    no_compatible_node: 'No compatible host',
  },
};

const COMFY_ROLES: Record<Locale, Record<string, string>> = {
  'zh-TW': {
    diffusion: '主模型',
    checkpoint: 'Checkpoint',
    text_encoder: '文字編碼',
    lora: 'LoRA',
    vae: 'VAE',
    controlnet: 'ControlNet',
    clip_vision: 'CLIP Vision',
    upscale: '放大模型',
  },
  en: {
    diffusion: 'Diffusion',
    checkpoint: 'Checkpoint',
    text_encoder: 'Text Encoder',
    lora: 'LoRA',
    vae: 'VAE',
    controlnet: 'ControlNet',
    clip_vision: 'CLIP Vision',
    upscale: 'Upscaler',
  },
};

const BACKEND_EXACT_EN: Record<string, string> = {
  '服務要求 API 認證': 'Service requires API authentication',
  '回應不符合此服務格式': 'Unexpected response format for this service',
  '連線逾時': 'Connection timed out',
  '無法建立連線': 'Unable to establish connection',
  '已找到可連線的服務。': 'Reachable services found.',
  '未找到可用服務，請確認遠端服務已啟動並允許區網連線。': 'No reachable services found. Check that remote services are running and accessible on the LAN.',
  '請輸入主機 IP 或名稱，不含路徑或帳密。': 'Enter a host IP or hostname without path or credentials.',
  '此主機名稱或位址已加入。': 'This host name or address is already registered.',
  '請更新主機 Agent 以支援模型載入與卸載。': 'Update the host agent to enable model load/unload controls.',
  '主機目前離線或停止接單，無法操作模型。': 'Host is offline or in maintenance; model operations are disabled.',
  '尚未連線至 ComfyUI 服務，無法操作模型。': 'ComfyUI service is offline; model operations are disabled.',
  '尚未取得執行器狀態，請等待 Agent 更新。': 'Runtime status unavailable; waiting for agent sync.',
  '模擬任務完成。': 'Simulated job completed.',
  'Agent 已停止模擬工作。': 'Agent stopped the simulated job.',
  '操作密碼長度需至少 4 個字元。': 'Operation password must be at least 4 characters.',
  '操作密碼長度不可超過 128 個字元。': 'Operation password cannot exceed 128 characters.',
  '主機操作密碼錯誤。': 'Incorrect host operation password.',
  '目前密碼錯誤。': 'Current password is incorrect.',
  '模擬主機不支援操作密碼設定。': 'Simulated hosts do not support operation passwords.',
  '此主機已啟用操作密碼保護，請先驗證密碼以解鎖模型操作。': 'This host requires password verification before running model operations.',
  '密碼嘗試次數過多，請稍後再試。': 'Too many failed password attempts. Please try again shortly.',
};

function translateBackendString(text: string, locale: Locale): string {
  if (locale === 'zh-TW' || !text) return text;
  if (BACKEND_EXACT_EN[text]) return BACKEND_EXACT_EN[text];
  const otherProcs = text.match(/^其他\s+(\d+)\s+個程序$/);
  if (otherProcs) return `${otherProcs[1]} other processes`;
  const reqFailed = text.match(/^請求失敗\s*\((\d+)\)$/);
  if (reqFailed) return `Request failed (${reqFailed[1]})`;
  return text;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readInitialLocale);

  const setLocale = useCallback((next: Locale) => {
    setLocaleState(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Ignore storage write errors.
    }
  }, []);

  const toggleLocale = useCallback(() => {
    setLocale(locale === 'zh-TW' ? 'en' : 'zh-TW');
  }, [locale, setLocale]);

  useEffect(() => {
    document.documentElement.lang = locale === 'zh-TW' ? 'zh-Hant' : 'en';
    document.title = locale === 'zh-TW'
      ? 'GPU Control Center · 中央推論平台'
      : 'GPU Control Center · Inference Console';
  }, [locale]);

  const value = useMemo<I18nContextValue>(() => {
    const isZh = locale === 'zh-TW';
    const dateLocale = isZh ? 'zh-TW' : 'en-GB';
    return {
      locale,
      isZh,
      setLocale,
      toggleLocale,
      t: (zh: string, en: string) => (isZh ? zh : en),
      formatTime: (iso: string) => new Date(iso).toLocaleString(dateLocale, { hour12: false }),
      formatClock: (iso: string) => new Date(iso).toLocaleTimeString(dateLocale, { hour12: false }),
      formatNowClock: () => new Date().toLocaleTimeString(dateLocale, { hour12: false }),
      statusLabel: (status: string) => STATUS_LABELS[locale][status] || status,
      blockerLabel: (blocker: string) => BLOCKER_LABELS[locale][blocker] || blocker,
      comfyRoleLabel: (role: string) => COMFY_ROLES[locale][role] || (isZh ? '模型' : 'Model'),
      processCategoryLabel: (category: string) => {
        if (category === 'comfyui') return 'ComfyUI';
        if (category === 'lmstudio') return 'LM Studio';
        if (category === 'ollama') return 'Ollama';
        return isZh ? '外部程序' : 'External';
      },
      translateApiText: (text: string) => translateBackendString(text, locale),
    };
  }, [locale, setLocale, toggleLocale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) {
    throw new Error('useI18n must be used inside I18nProvider');
  }
  return ctx;
}
