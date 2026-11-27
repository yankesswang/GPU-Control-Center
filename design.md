# GPU Control Center design system

Approved direction: 深色工作台，保留綠色識別。2026-11-27 使用者補充要求：硬體狀態用形象化圖示呈現。

## Principles

Modern-minimal Workbench，桌機固定功能側欄、手機四個精簡導覽。優先呈現真實設備與生成結果，工程細節放在展開區塊。每張 GPU 以靜態外形示意、實際負載圓環、顯存容量格、溫度與功耗呈現。示意風扇不代表轉速。CPU 與 RAM 使用晶片圖形；窄手機保留讀數與 GPU 圖示。

IBM Plex Sans 用於操作文字、IBM Plex Mono 用於數字與模型，中文依可用字型回退。4px 間距尺度，44px 控制元件，主操作綠底深字。動態只保留按壓、重新整理與 dialog，支援 reduced-motion。原生 dialog 處理焦點與 Escape。資料缺失顯示破折號，模擬資料明確標示。

保留四個功能頁、API 與主機工作槽語意。硬體讀數包含其他程序，中央目前不能保證指定 GPU 或保留 VRAM。既有 style.css 保留，以 console.css 與 tokens.css 覆蓋。

## Model and service controls

每台主機提供 LM Studio／Ollama 預設選單並持久化；總覽用精簡模型選單與載入／卸載按鈕，模型頁保留完整清單。處理中按鈕停用並顯示狀態，不捏造載入百分比。ComfyUI 為獨立唯讀區塊，顯示連線、佇列、分類檔案與裝置資料，不將可用檔案當成常駐模型。沿用既有 token、原生 select/details 與 Phosphor 圖示。

## Runtime model inventory

設備圖卡上方顯示此主機 Ollama 服務的實際常駐模型，與已安裝模型清單分開。模型名稱可斷行，顯存與載入大小以 GiB 顯示。空清單、取得失敗、舊 Agent 尚未回報、主機離線快照各自標示。

## Validation

Production TypeScript/Vite build、19 個 API 測試、MCP 真實推論 smoke 通過。GUI 實測新建推論並收到結果（212325e3，8 tokens）。已查看 1280px 桌面與 320/375/414/768px 響應式；搜尋空狀態、清除篩選、模型頁與 dialog 已檢查。

Palette contrast calculations (WCAG linear-sRGB from OKLCH):

- muted / paper-3: 6.40:1
- accent-ink / accent: 10.94:1
- focus / paper: 5.09:1
- focus / paper-2: 4.78:1
- ink-2 / paper-3: 9.75:1
- warning / warning-soft: 9.78:1
- error / error-soft: 7.73:1
- info / info-soft: 8.98:1
- success / accent-soft: 8.91:1

## Exports

Canonical source: `tokens.css`; following adapters are documentation, not additional runtime dependencies.

### CSS

```css
/* Hallmark · macrostructure: Workbench · tone: technical · anchor hue: green
 * genre: modern-minimal · theme: approved dark console · design-system: design.md
 * pre-emit critique: P4 H4 E4 S5 R4 V4 */
:root {
  color-scheme: dark;
  --color-paper: oklch(16.5% 0.008 160);
  --color-paper-2: oklch(20% 0.008 160);
  --color-paper-3: oklch(24% 0.010 160);
  --color-sidebar: oklch(14.5% 0.010 160);
  --color-ink: oklch(95% 0.008 160);
  --color-ink-2: oklch(83% 0.010 160);
  --color-muted: oklch(71% 0.012 160);
  --color-rule: oklch(31% 0.012 160);
  --color-rule-2: oklch(40% 0.014 160);
  --color-accent: oklch(81% 0.14 155);
  --color-accent-hover: oklch(86% 0.12 155);
  --color-accent-ink: oklch(18% 0.025 155);
  --color-accent-soft: oklch(26% 0.035 155);
  --color-focus: oklch(61% 0.14 250);
  --color-success: oklch(81% 0.12 155);
  --color-warning: oklch(85% 0.12 85);
  --color-warning-soft: oklch(26% 0.025 85);
  --color-error: oklch(79% 0.12 25);
  --color-error-soft: oklch(26% 0.025 25);
  --color-info: oklch(82% 0.09 240);
  --color-info-soft: oklch(26% 0.025 240);
  --color-overlay: oklch(9% 0.008 160 / 0.78);
  --color-transparent: transparent;
  --font-display: 'IBM Plex Mono', 'Noto Sans TC', monospace;
  --font-body: 'IBM Plex Sans', 'Noto Sans TC', system-ui, sans-serif;
  --font-mono: var(--font-display);
  --font-outlier: var(--font-display);
  --space-3xs: 0.25rem;
  --space-2xs: 0.5rem;
  --space-xs: 0.75rem;
  --space-sm: 1rem;
  --space-md: 1.5rem;
  --space-lg: 2rem;
  --space-xl: 3rem;
  --space-2xl: 4rem;
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-xl: 1.5rem;
  --text-2xl: 2rem;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --dur-micro: 100ms;
  --dur-short: 180ms;
  --dur-long: 260ms;
  --radius-card: 12px;
  --radius-input: 8px;
  --radius-pill: 999px;
  --rule-fine: 1px;
  --rule-focus: 2px;
  --size-control: 2.75rem;
  --size-sidebar: 14.5rem;
  --size-topbar: 4rem;
  --z-header: 20;
  --shadow-modal: 0 24px 100px oklch(8% 0.008 160 / 0.65);
  --accent: var(--color-accent);
  --border: var(--color-rule);
  --muted: var(--color-muted);
}
```

### Tailwind v4

```css
@theme {
  --color-paper: oklch(16.5% 0.008 160);
  --color-paper-2: oklch(20% 0.008 160);
  --color-paper-3: oklch(24% 0.010 160);
  --color-sidebar: oklch(14.5% 0.010 160);
  --color-ink: oklch(95% 0.008 160);
  --color-ink-2: oklch(83% 0.010 160);
  --color-muted: oklch(71% 0.012 160);
  --color-rule: oklch(31% 0.012 160);
  --color-rule-2: oklch(40% 0.014 160);
  --color-accent: oklch(81% 0.14 155);
  --color-accent-hover: oklch(86% 0.12 155);
  --color-accent-ink: oklch(18% 0.025 155);
  --color-accent-soft: oklch(26% 0.035 155);
  --color-focus: oklch(61% 0.14 250);
  --color-success: oklch(81% 0.12 155);
  --color-warning: oklch(85% 0.12 85);
  --color-warning-soft: oklch(26% 0.025 85);
  --color-error: oklch(79% 0.12 25);
  --color-error-soft: oklch(26% 0.025 25);
  --color-info: oklch(82% 0.09 240);
  --color-info-soft: oklch(26% 0.025 240);
  --color-overlay: oklch(9% 0.008 160 / 0.78);
  --color-transparent: transparent;
  --font-display: 'IBM Plex Mono', 'Noto Sans TC', monospace;
  --font-body: 'IBM Plex Sans', 'Noto Sans TC', system-ui, sans-serif;
  --font-mono: var(--font-display);
  --font-outlier: var(--font-display);
  --spacing-3xs: 0.25rem;
  --spacing-2xs: 0.5rem;
  --spacing-xs: 0.75rem;
  --spacing-sm: 1rem;
  --spacing-md: 1.5rem;
  --spacing-lg: 2rem;
  --spacing-xl: 3rem;
  --spacing-2xl: 4rem;
  --text-xs: 0.75rem;
  --text-sm: 0.875rem;
  --text-base: 1rem;
  --text-xl: 1.5rem;
  --text-2xl: 2rem;
  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-in: cubic-bezier(0.7, 0, 0.84, 0);
  --ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);
  --radius-card: 12px;
  --radius-input: 8px;
  --radius-pill: 999px;
}
```

### DTCG JSON

```json
{
  "color": {
    "paper": {
      "$type": "color",
      "$value": "oklch(16.5% 0.008 160)"
    },
    "paper-2": {
      "$type": "color",
      "$value": "oklch(20% 0.008 160)"
    },
    "paper-3": {
      "$type": "color",
      "$value": "oklch(24% 0.010 160)"
    },
    "sidebar": {
      "$type": "color",
      "$value": "oklch(14.5% 0.010 160)"
    },
    "ink": {
      "$type": "color",
      "$value": "oklch(95% 0.008 160)"
    },
    "ink-2": {
      "$type": "color",
      "$value": "oklch(83% 0.010 160)"
    },
    "muted": {
      "$type": "color",
      "$value": "oklch(71% 0.012 160)"
    },
    "rule": {
      "$type": "color",
      "$value": "oklch(31% 0.012 160)"
    },
    "rule-2": {
      "$type": "color",
      "$value": "oklch(40% 0.014 160)"
    },
    "accent": {
      "$type": "color",
      "$value": "oklch(81% 0.14 155)"
    },
    "accent-hover": {
      "$type": "color",
      "$value": "oklch(86% 0.12 155)"
    },
    "accent-ink": {
      "$type": "color",
      "$value": "oklch(18% 0.025 155)"
    },
    "accent-soft": {
      "$type": "color",
      "$value": "oklch(26% 0.035 155)"
    },
    "focus": {
      "$type": "color",
      "$value": "oklch(61% 0.14 250)"
    },
    "success": {
      "$type": "color",
      "$value": "oklch(81% 0.12 155)"
    },
    "warning": {
      "$type": "color",
      "$value": "oklch(85% 0.12 85)"
    },
    "warning-soft": {
      "$type": "color",
      "$value": "oklch(26% 0.025 85)"
    },
    "error": {
      "$type": "color",
      "$value": "oklch(79% 0.12 25)"
    },
    "error-soft": {
      "$type": "color",
      "$value": "oklch(26% 0.025 25)"
    },
    "info": {
      "$type": "color",
      "$value": "oklch(82% 0.09 240)"
    },
    "info-soft": {
      "$type": "color",
      "$value": "oklch(26% 0.025 240)"
    },
    "overlay": {
      "$type": "color",
      "$value": "oklch(9% 0.008 160 / 0.78)"
    },
    "transparent": {
      "$type": "color",
      "$value": "transparent"
    }
  },
  "font": {
    "display": {
      "$type": "fontFamily",
      "$value": "'IBM Plex Mono', 'Noto Sans TC', monospace"
    },
    "body": {
      "$type": "fontFamily",
      "$value": "'IBM Plex Sans', 'Noto Sans TC', system-ui, sans-serif"
    }
  },
  "space": {
    "3xs": {
      "$type": "dimension",
      "$value": "0.25rem"
    },
    "2xs": {
      "$type": "dimension",
      "$value": "0.5rem"
    },
    "xs": {
      "$type": "dimension",
      "$value": "0.75rem"
    },
    "sm": {
      "$type": "dimension",
      "$value": "1rem"
    },
    "md": {
      "$type": "dimension",
      "$value": "1.5rem"
    },
    "lg": {
      "$type": "dimension",
      "$value": "2rem"
    },
    "xl": {
      "$type": "dimension",
      "$value": "3rem"
    },
    "2xl": {
      "$type": "dimension",
      "$value": "4rem"
    }
  },
  "text": {
    "xs": {
      "$type": "dimension",
      "$value": "0.75rem"
    },
    "sm": {
      "$type": "dimension",
      "$value": "0.875rem"
    },
    "base": {
      "$type": "dimension",
      "$value": "1rem"
    },
    "xl": {
      "$type": "dimension",
      "$value": "1.5rem"
    },
    "2xl": {
      "$type": "dimension",
      "$value": "2rem"
    }
  },
  "dur": {
    "micro": {
      "$type": "duration",
      "$value": "100ms"
    },
    "short": {
      "$type": "duration",
      "$value": "180ms"
    },
    "long": {
      "$type": "duration",
      "$value": "260ms"
    }
  },
  "radius": {
    "card": {
      "$type": "dimension",
      "$value": "12px"
    },
    "input": {
      "$type": "dimension",
      "$value": "8px"
    },
    "pill": {
      "$type": "dimension",
      "$value": "999px"
    }
  },
  "rule": {
    "fine": {
      "$type": "dimension",
      "$value": "1px"
    },
    "focus": {
      "$type": "dimension",
      "$value": "2px"
    }
  }
}
```

### shadcn CSS role mapping

```css
:root {
  --background: oklch(16.5% 0.008 160);
  --foreground: oklch(95% 0.008 160);
  --card: oklch(20% 0.008 160);
  --card-foreground: oklch(95% 0.008 160);
  --popover: oklch(20% 0.008 160);
  --popover-foreground: oklch(95% 0.008 160);
  --primary: oklch(81% 0.14 155);
  --primary-foreground: oklch(18% 0.025 155);
  --secondary: oklch(24% 0.010 160);
  --secondary-foreground: oklch(83% 0.010 160);
  --muted: oklch(31% 0.012 160);
  --muted-foreground: oklch(71% 0.012 160);
  --accent: oklch(81% 0.14 155);
  --accent-foreground: oklch(18% 0.025 155);
  --destructive: oklch(79% 0.12 25);
  --border: oklch(31% 0.012 160);
  --input: oklch(40% 0.014 160);
  --ring: oklch(61% 0.14 250);
  --radius: 12px;
}
```
