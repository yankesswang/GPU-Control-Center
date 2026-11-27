import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Check,
  CircleNotch,
  Code,
  Copy,
  Key,
  LockSimple,
  LockSimpleOpen,
  PlugsConnected,
  Plus,
  Prohibit,
  ShieldCheck,
  Terminal,
  Trash,
  WarningCircle,
  X,
} from '@phosphor-icons/react';
import {
  api,
  type McpCreatedToken,
  type McpInfo,
  type McpRole,
  type McpToken,
} from '../api';
import { useI18n } from '../i18n';
import { Empty, Skeleton } from './ui';

type ClientPreset = 'claude-code' | 'codex' | 'cursor' | 'claude-desktop' | 'python' | 'curl';

function CopySnippetButton({ text, label }: { text: string; label?: string }) {
  const { t } = useI18n();
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(id);
  }, [copied]);
  return (
    <button
      type="button"
      className="gcc-button gcc-button--quiet gcc-btn-sm"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
        } catch {
          // Ignore clipboard errors in non-secure contexts
        }
      }}
    >
      {copied ? <Check size={14} /> : <Copy size={14} />}
      {copied ? t('已複製', 'Copied') : (label || t('複製', 'Copy'))}
    </button>
  );
}

function roleLabel(role: McpRole, t: (zh: string, en: string) => string): string {
  if (role === 'read') return t('唯讀監控 (read)', 'Read-Only (read)');
  if (role === 'admin') return t('完整管理 (admin)', 'Full Control (admin)');
  return t('推論執行 (infer)', 'Inference (infer)');
}

export function McpPanel() {
  const { isZh, t, formatTime, translateApiText } = useI18n();
  const [info, setInfo] = useState<McpInfo | null>(null);
  const [tokens, setTokens] = useState<McpToken[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const [creating, setCreating] = useState(false);
  const [freshToken, setFreshToken] = useState<McpCreatedToken | null>(null);
  const [preset, setPreset] = useState<ClientPreset>('claude-code');
  const [roleFilter, setRoleFilter] = useState<'all' | McpRole>('all');
  const [endpointUrl, setEndpointUrl] = useState('');
  const endpointEdited = useRef(false);

  const load = useCallback(async () => {
    try {
      const [mcpInfo, tokenList] = await Promise.all([
        api<McpInfo>('/mcp/info'),
        api<McpToken[]>('/mcp/tokens'),
      ]);
      setInfo(mcpInfo);
      setTokens(tokenList);
      setError('');
      if (!endpointEdited.current && typeof window !== 'undefined') {
        const host = window.location.hostname || 'localhost';
        setEndpointUrl(`http://${host}:${mcpInfo.default_port}${mcpInfo.path}`);
      }
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('無法載入 MCP 設定', 'Failed to load MCP settings'));
    } finally {
      setLoading(false);
    }
  }, [t, translateApiText]);

  useEffect(() => {
    void load();
    const id = setInterval(() => void load(), 10000);
    return () => clearInterval(id);
  }, [load]);

  const directUrl = useMemo(() => {
    const port = info?.default_port ?? 8090;
    const host = typeof window !== 'undefined' ? (window.location.hostname || 'localhost') : 'localhost';
    return `http://${host}:${port}/mcp`;
  }, [info]);

  const proxyUrl = useMemo(() => {
    if (typeof window === 'undefined') return 'http://localhost:5180/mcp';
    return `${window.location.origin}/mcp`;
  }, []);

  const snippetToken = freshToken?.token || (info?.auth_enabled ? 'gcc_mcp_YOUR_TOKEN_HERE' : '');
  const cleanUrl = (endpointUrl || directUrl).trim();

  const snippet = useMemo(() => {
    const authHeader = snippetToken ? `Authorization: Bearer ${snippetToken}` : '';
    switch (preset) {
      case 'claude-code':
        return snippetToken
          ? `claude mcp add --transport http gpu-control-center \\\n  ${cleanUrl} \\\n  --header "${authHeader}"`
          : `claude mcp add --transport http gpu-control-center ${cleanUrl}`;
      case 'codex':
        return snippetToken
          ? `export GPU_MCP_TOKEN="${snippetToken}"\ncodex mcp add gpu-control-center \\\n  --url ${cleanUrl} \\\n  --bearer-token-env-var GPU_MCP_TOKEN`
          : `codex mcp add gpu-control-center --url ${cleanUrl}`;
      case 'cursor': {
        const serverObj: Record<string, unknown> = { url: cleanUrl };
        if (snippetToken) {
          serverObj.headers = { Authorization: `Bearer ${snippetToken}` };
        }
        return JSON.stringify({ mcpServers: { 'gpu-control-center': serverObj } }, null, 2);
      }
      case 'claude-desktop': {
        const envObj: Record<string, string> = { GPU_CONTROL_CENTER_MCP_URL: cleanUrl };
        if (snippetToken) envObj.GPU_CONTROL_CENTER_MCP_TOKEN = snippetToken;
        return JSON.stringify(
          {
            mcpServers: {
              'gpu-control-center': {
                command: 'uvx',
                args: [
                  '--from',
                  'git+https://github.com/yankesswang/GPU-Control-Center.git#subdirectory=mcp-package',
                  'gpu-control-center-mcp',
                ],
                env: envObj,
              },
            },
          },
          null,
          2,
        );
      }
      case 'python': {
        const headerLine = snippetToken
          ? `    headers = {"Authorization": "Bearer ${snippetToken}"}\n`
          : '    headers = None\n';
        return (
          `import asyncio\nfrom mcp import ClientSession\nfrom mcp.client.streamable_http import streamablehttp_client\n\n` +
          `async def main():\n` +
          `    url = "${cleanUrl}"\n` +
          headerLine +
          `    async with streamablehttp_client(url, headers=headers) as (read, write, _):\n` +
          `        async with ClientSession(read, write) as session:\n` +
          `            await session.initialize()\n` +
          `            res = await session.call_tool("run_inference", {\n` +
          `                "prompt": "用三點說明集中式 GPU 排程的優勢。",\n` +
          `                "max_tokens": 256,\n` +
          `            })\n` +
          `            print(res.structuredContent["text"])\n\n` +
          `asyncio.run(main())`
        );
      }
      case 'curl': {
        const authFlag = snippetToken ? ` \\\n  -H "Authorization: Bearer ${snippetToken}"` : '';
        return (
          `curl -sS -X POST "${cleanUrl}"${authFlag} \\\n` +
          `  -H "Content-Type: application/json" \\\n` +
          `  -H "Accept: application/json, text/event-stream" \\\n` +
          `  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'`
        );
      }
    }
  }, [preset, cleanUrl, snippetToken]);

  async function revokeToken(tok: McpToken) {
    setBusy(true);
    setError('');
    try {
      await api(`/mcp/tokens/${tok.id}/revoke`, 'POST');
      await load();
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('撤銷失敗', 'Failed to revoke token'));
    } finally {
      setBusy(false);
    }
  }

  async function deleteToken(tok: McpToken) {
    setBusy(true);
    setError('');
    try {
      await api(`/mcp/tokens/${tok.id}`, 'DELETE');
      if (freshToken?.id === tok.id) setFreshToken(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? translateApiText(e.message) : t('刪除失敗', 'Failed to delete token'));
    } finally {
      setBusy(false);
    }
  }

  if (loading && !info) return <Skeleton rows={5} />;

  const filteredTools = (info?.tools || []).filter(tool => roleFilter === 'all' || tool.role === roleFilter);

  return (
    <section className="gcc-mcp-workspace" aria-label={t('MCP 外部 Agent 接入中心', 'MCP External Agent Hub')}>
      {error && (
        <div className="gcc-alert" role="alert">
          <WarningCircle size={20} />
          <p>{error}</p>
          <button className="gcc-icon-button" aria-label={t('關閉錯誤', 'Dismiss')} onClick={() => setError('')}>
            <X size={18} />
          </button>
        </div>
      )}

      {info && (
        <div className="gcc-summary" aria-label={t('MCP 狀態摘要', 'MCP status summary')}>
          <div className={`gcc-stat ${info.auth_enabled ? 'gcc-stat--online is-highlighted' : 'gcc-stat--queued'}`}>
            <span>
              <span className="gcc-stat-icon" aria-hidden="true">
                {info.auth_enabled ? <LockSimple size={15} /> : <LockSimpleOpen size={15} />}
              </span>
              {t('認證模式', 'Auth Mode')}
            </span>
            <div>
              <strong>{info.auth_enabled ? t('Token 保護', 'Protected') : t('本機開放', 'Open Local')}</strong>
              <span>{info.auth_enabled ? 'Bearer / API Key' : t('建立 Token 即自動啟用', 'Auto-enforced on first token')}</span>
            </div>
          </div>
          <div className={`gcc-stat gcc-stat--running${info.active_tokens > 0 ? ' is-highlighted' : ''}`}>
            <span>
              <span className="gcc-stat-icon" aria-hidden="true"><Key size={15} /></span>
              {t('有效 Token', 'Active Tokens')}
            </span>
            <div>
              <strong>{info.active_tokens}</strong>
              <span>/ {info.total_tokens} {t('組', 'total')}</span>
            </div>
          </div>
          <div className="gcc-stat gcc-stat--completed">
            <span>
              <span className="gcc-stat-icon" aria-hidden="true"><PlugsConnected size={15} /></span>
              {t('累計驗證請求', 'Authenticated Calls')}
            </span>
            <div>
              <strong>{info.total_requests}</strong>
              <span>{t('次', 'calls')}</span>
            </div>
          </div>
          <div className="gcc-stat gcc-stat--online">
            <span>
              <span className="gcc-stat-icon" aria-hidden="true"><Terminal size={15} /></span>
              {t('開放工具數', 'Exposed Tools')}
            </span>
            <div>
              <strong>{info.tools.length}</strong>
              <span>+ {info.resources.length} {t('資源', 'resources')}</span>
            </div>
          </div>
        </div>
      )}

      {freshToken && (
        <div className="gcc-mcp-fresh-banner" role="status">
          <div className="gcc-mcp-fresh-head">
            <div>
              <strong><ShieldCheck size={18} /> {t('新 API Token 已發行（僅顯示這一次）', 'New API Token Issued (Shown Once)')}</strong>
              <p>
                {t(
                  `已為「${freshToken.name}」發行 ${roleLabel(freshToken.role, t)} 權限 Token，來源標記為 ${freshToken.source_tag}。下方所有接入範例已自動帶入此金鑰。`,
                  `Issued ${roleLabel(freshToken.role, t)} token for "${freshToken.name}" (source: ${freshToken.source_tag}). All client snippets below are pre-filled with this key.`,
                )}
              </p>
            </div>
            <button
              type="button"
              className="gcc-icon-button"
              aria-label={t('關閉一次性金鑰提示', 'Dismiss token banner')}
              onClick={() => setFreshToken(null)}
            >
              <X size={18} />
            </button>
          </div>
          <div className="gcc-mcp-token-box">
            <code>{freshToken.token}</code>
            <CopySnippetButton text={freshToken.token} label={t('複製 Token', 'Copy Token')} />
          </div>
        </div>
      )}

      <div className="gcc-mcp-grid">
        <article className="gcc-panel gcc-mcp-card">
          <header className="gcc-panel-head">
            <div>
              <h2><Code size={18} /> {t('快速接入外部 Agent 系統', 'Connect External Agent Systems')}</h2>
              <p>{t('支援 Streamable HTTP 直連與獨立 stdio 橋接套件，複製即可接入。', 'Supports Streamable HTTP direct connection and standalone stdio bridge package.')}</p>
            </div>
          </header>

          <div className="gcc-mcp-endpoint-row">
            <label className="gcc-field">
              <span>{t('MCP 端點位址（會即時套用到下方範例）', 'MCP Endpoint URL (live-applied to snippets below)')}</span>
              <input
                value={endpointUrl}
                onChange={e => {
                  endpointEdited.current = true;
                  setEndpointUrl(e.target.value);
                }}
                placeholder="http://192.168.1.10:8090/mcp"
              />
            </label>
            <div className="gcc-mcp-url-pills">
              <button
                type="button"
                className={`gcc-button gcc-button--quiet gcc-btn-sm${cleanUrl === directUrl ? ' is-selected' : ''}`}
                onClick={() => {
                  endpointEdited.current = true;
                  setEndpointUrl(directUrl);
                }}
              >
                {t('直連 MCP 埠', 'Direct MCP Port')}
              </button>
              <button
                type="button"
                className={`gcc-button gcc-button--quiet gcc-btn-sm${cleanUrl === proxyUrl ? ' is-selected' : ''}`}
                onClick={() => {
                  endpointEdited.current = true;
                  setEndpointUrl(proxyUrl);
                }}
              >
                {t('同源 /mcp 代理', 'Same-Origin /mcp Proxy')}
              </button>
            </div>
          </div>

          <div className="gcc-mcp-tabs" role="tablist" aria-label={t('選擇 Agent 平台', 'Select agent platform')}>
            {([
              ['claude-code', 'Claude Code'],
              ['codex', 'Codex CLI'],
              ['cursor', 'Cursor / Windsurf'],
              ['claude-desktop', 'Claude Desktop (uvx)'],
              ['python', 'Python SDK'],
              ['curl', 'cURL'],
            ] as [ClientPreset, string][]).map(([id, label]) => (
              <button
                key={id}
                type="button"
                role="tab"
                aria-selected={preset === id}
                className={`gcc-range-btn${preset === id ? ' is-active' : ''}`}
                onClick={() => setPreset(id)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="gcc-mcp-snippet-wrap">
            <div className="gcc-mcp-snippet-bar">
              <span>{preset === 'claude-desktop' ? 'stdio bridge (uvx)' : 'Streamable HTTP'}</span>
              <CopySnippetButton text={snippet} label={t('複製設定', 'Copy Config')} />
            </div>
            <pre className="gcc-mcp-code"><code>{snippet}</code></pre>
          </div>

          <p className="gcc-mcp-hint">
            {t(
              '跨主機／區網開放提醒：請在 .env 設定 MCP_BIND_HOST=0.0.0.0 與 MCP_ALLOWED_HOSTS=*（或指定主機白名單），並至少發行一組 Token 以強制 Bearer 驗證。',
              'LAN / external exposure note: set MCP_BIND_HOST=0.0.0.0 and MCP_ALLOWED_HOSTS=* (or explicit hosts) in .env, and issue at least one token to enforce Bearer auth.',
            )}
          </p>
        </article>

        <article className="gcc-panel gcc-mcp-card">
          <header className="gcc-panel-head">
            <div>
              <h2><Key size={18} /> {t('Agent API Token 管理', 'Agent API Tokens')}</h2>
              <p>{t('為不同外部 Agent 發行獨立金鑰、分級權限並自動標記任務來源。', 'Issue scoped Bearer keys per external agent with role permissions and source attribution.')}</p>
            </div>
            <button type="button" className="gcc-button gcc-button--primary gcc-btn-sm" onClick={() => setCreating(true)}>
              <Plus size={15} /> {t('發行 Token', 'Issue Token')}
            </button>
          </header>

          {tokens.length === 0 ? (
            <Empty icon={<Key size={26} />} title={t('尚未發行外部 Agent Token', 'No MCP Tokens Issued Yet')}>
              {t(
                '目前為本機開放模式。發行第一組 Token 後，所有 HTTP MCP 連線將自動啟用 Bearer Token 認證與角色權限控制。',
                'Currently running in open local mode. Issuing your first token automatically enforces Bearer authentication and RBAC on all HTTP MCP connections.',
              )}
            </Empty>
          ) : (
            <div className="gcc-bill-table-wrap">
              <table className="gcc-bill-table">
                <thead>
                  <tr>
                    <th>{t('名稱 / 來源', 'Agent / Source')}</th>
                    <th>{t('權限角色', 'Role')}</th>
                    <th>{t('金鑰前綴', 'Prefix')}</th>
                    <th>{t('使用紀錄', 'Usage')}</th>
                    <th>{t('狀態', 'Status')}</th>
                    <th className="gcc-bill-actions">{t('操作', 'Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map(tok => (
                    <tr key={tok.id} className={tok.active ? '' : 'is-revoked'}>
                      <td>
                        <strong>{tok.name}</strong>
                        <span className="gcc-mono-sub">{tok.source_tag}</span>
                        {tok.notes && <span className="gcc-bill-note">{tok.notes}</span>}
                      </td>
                      <td>
                        <span className={`gcc-mcp-role gcc-mcp-role--${tok.role}`}>{tok.role}</span>
                      </td>
                      <td className="gcc-mono-cell">
                        <code>{tok.token_prefix}…</code>
                      </td>
                      <td className="gcc-mono-cell">
                        {tok.request_count} {t('次', 'calls')}
                        <small>{tok.last_used_at ? formatTime(tok.last_used_at) : t('尚未使用', 'Never used')}</small>
                      </td>
                      <td>
                        <span className={`gcc-mcp-state ${tok.active ? 'is-active' : 'is-revoked'}`}>
                          {tok.active ? t('啟用中', 'Active') : t('已撤銷', 'Revoked')}
                        </span>
                      </td>
                      <td className="gcc-bill-actions">
                        {tok.active && (
                          <button
                            type="button"
                            className="gcc-icon-button"
                            disabled={busy}
                            title={t('撤銷此 Token', 'Revoke token')}
                            aria-label={t(`撤銷 ${tok.name}`, `Revoke ${tok.name}`)}
                            onClick={() => void revokeToken(tok)}
                          >
                            <Prohibit size={16} />
                          </button>
                        )}
                        <button
                          type="button"
                          className="gcc-icon-button gcc-icon-danger"
                          disabled={busy}
                          title={t('永久刪除紀錄', 'Delete token record')}
                          aria-label={t(`刪除 ${tok.name}`, `Delete ${tok.name}`)}
                          onClick={() => void deleteToken(tok)}
                        >
                          <Trash size={16} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>
      </div>

      {info && (
        <article className="gcc-panel gcc-mcp-card">
          <header className="gcc-panel-head">
            <div>
              <h2><Terminal size={18} /> {t('MCP 工具、資源與提示詞目錄', 'MCP Tools, Resources & Prompts')}</h2>
              <p>{t('依角色自動授權：read 唯讀查詢、infer 執行與管理推論、admin 控制模型載卸與主機維護。', 'Role-gated capabilities: read for discovery, infer for jobs, admin for model & host control.')}</p>
            </div>
            <div className="gcc-mcp-role-filter" role="group" aria-label={t('依角色篩選工具', 'Filter tools by role')}>
              {(['all', 'read', 'infer', 'admin'] as const).map(r => (
                <button
                  key={r}
                  type="button"
                  className={`gcc-range-btn${roleFilter === r ? ' is-active' : ''}`}
                  onClick={() => setRoleFilter(r)}
                >
                  {r === 'all' ? t('全部', 'All') : r}
                </button>
              ))}
            </div>
          </header>

          <div className="gcc-mcp-tool-grid">
            {filteredTools.map(tool => (
              <div className="gcc-mcp-tool-item" key={tool.name}>
                <div className="gcc-mcp-tool-top">
                  <code>{tool.name}</code>
                  <span className={`gcc-mcp-role gcc-mcp-role--${tool.role}`}>{tool.role}</span>
                </div>
                <p>{isZh ? tool.summary_zh : tool.summary_en}</p>
              </div>
            ))}
          </div>

          <div className="gcc-mcp-extras">
            <div>
              <h3>{t('MCP Resources（唯讀資料端點）', 'MCP Resources (Read-Only URIs)')}</h3>
              <ul>
                {info.resources.map(res => (
                  <li key={res.uri}>
                    <code>{res.uri}</code>
                    <span>{isZh ? res.summary_zh : res.summary_en}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3>{t('MCP Prompts（內建引導範本）', 'MCP Prompts (Built-In Templates)')}</h3>
              <ul>
                {info.prompts.map(pr => (
                  <li key={pr.name}>
                    <code>{pr.name}</code>
                    <span>{isZh ? pr.summary_zh : pr.summary_en}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </article>
      )}

      {creating && (
        <IssueTokenDialog
          onClose={() => setCreating(false)}
          onCreated={async created => {
            setCreating(false);
            setFreshToken(created);
            await load();
          }}
        />
      )}
    </section>
  );
}

function IssueTokenDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (token: McpCreatedToken) => Promise<void>;
}) {
  const { t, translateApiText } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const [name, setName] = useState('');
  const [sourceTag, setSourceTag] = useState('');
  const [role, setRole] = useState<McpRole>('infer');
  const [notes, setNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!name.trim()) return;
    setSubmitting(true);
    setError('');
    try {
      const created = await api<McpCreatedToken>('/mcp/tokens', 'POST', {
        name: name.trim(),
        source_tag: sourceTag.trim() || null,
        role,
        notes: notes.trim() || null,
      });
      await onCreated(created);
    } catch (err) {
      setError(err instanceof Error ? translateApiText(err.message) : t('發行失敗', 'Failed to issue token'));
      setSubmitting(false);
    }
  }

  return (
    <dialog
      ref={ref}
      className="gcc-dialog gcc-create-dialog"
      aria-labelledby="issue-mcp-token-title"
      onCancel={e => {
        if (submitting) e.preventDefault();
        else onClose();
      }}
    >
      <form onSubmit={e => void submit(e)}>
        <div className="gcc-dialog-head">
          <span><Key size={18} /> {t('發行 MCP API Token', 'Issue MCP API Token')}</span>
          <button type="button" className="gcc-icon-button" aria-label={t('關閉', 'Close')} onClick={onClose} disabled={submitting}>
            <X size={21} />
          </button>
        </div>
        <div className="gcc-create-body">
          <h2 id="issue-mcp-token-title">{t('建立外部 Agent 憑證', 'Create External Agent Credential')}</h2>
          <p className="gcc-create-description">
            {t('為外部 Agent 系統建立專屬金鑰。原始 Token 僅在建立完成當下顯示一次。', 'Create a scoped credential for an external agent system. The raw token is only displayed once.')}
          </p>
          <label className="gcc-field">
            <span>{t('Agent / 使用者名稱', 'Agent / Client Name')} <small>{t('必填', 'Required')}</small></span>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder={t('例如：Research Agent、團隊 Cursor', 'e.g. Research Agent, Team Cursor')}
              maxLength={100}
              required
              autoFocus
              disabled={submitting}
            />
          </label>
          <div className="gcc-form-grid">
            <label className="gcc-field">
              <span>{t('任務來源標記（選填）', 'Job Source Tag (Optional)')}</span>
              <input
                value={sourceTag}
                onChange={e => setSourceTag(e.target.value)}
                placeholder="mcp:research-agent"
                maxLength={80}
                disabled={submitting}
              />
              <span className="gcc-field-help">{t('留空將依名稱自動產生，顯示在任務佇列來源欄。', 'Auto-generated if blank; appears as the job source in the queue.')}</span>
            </label>
            <label className="gcc-field">
              <span>{t('權限角色', 'Permission Role')}</span>
              <select value={role} onChange={e => setRole(e.target.value as McpRole)} disabled={submitting}>
                <option value="read">{t('read — 僅查詢硬體、模型、任務與電費', 'read — Read-only telemetry, models, jobs & energy')}</option>
                <option value="infer">{t('infer — 推論執行、排程查詢、取消與重試（推薦）', 'infer — Inference, queue, cancel & retry (Recommended)')}</option>
                <option value="admin">{t('admin — 含模型載入卸載、切換執行器與維護模式', 'admin — Includes model load/unload & host controls')}</option>
              </select>
            </label>
          </div>
          <label className="gcc-field">
            <span>{t('備註（選填）', 'Notes (Optional)')}</span>
            <input
              value={notes}
              onChange={e => setNotes(e.target.value)}
              placeholder={t('用途說明或聯絡人', 'Purpose or owner contact')}
              maxLength={500}
              disabled={submitting}
            />
          </label>
          {error && (
            <div className="gcc-alert" role="alert">
              <WarningCircle size={19} />
              <p>{error}</p>
            </div>
          )}
        </div>
        <div className="gcc-create-actions">
          <span><ShieldCheck size={16} /> {t('僅儲存 SHA-256 雜湊值', 'Stored as SHA-256 hash only')}</span>
          <div>
            <button type="button" className="gcc-button" onClick={onClose} disabled={submitting}>{t('取消', 'Cancel')}</button>
            <button type="submit" className="gcc-button gcc-button--primary" disabled={submitting || !name.trim()}>
              {submitting ? <CircleNotch size={17} className="gcc-spin" /> : <Plus size={17} />}
              {submitting ? t('發行中', 'Issuing…') : t('發行 Token', 'Issue Token')}
            </button>
          </div>
        </div>
      </form>
    </dialog>
  );
}
