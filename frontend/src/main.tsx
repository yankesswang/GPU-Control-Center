import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {
  Pulse, IconContext, ArrowClockwise, BookOpen, Check, CircleNotch, Copy, Cpu,
  DesktopTower, Gauge, GitBranch, Info, Lightning, ListBullets, PlugsConnected, Plus,
  Queue, SlidersHorizontal, Stack, WarningCircle, X,
} from '@phosphor-icons/react';
import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';
import '@fontsource-variable/noto-sans-tc';
import {api, ApiError, modelDisplayName, type Job, type Model, type Node, type Overview, type Explanation, type SlaClass} from './api';
import {I18nProvider, useI18n} from './i18n';
import {HardwarePanel, SlotPanel} from './components/HardwarePanel';
import {HardwareHistoryPanel} from './components/HardwareHistoryPanel';
import {EnergyPanel} from './components/EnergyPanel';
import {McpPanel} from './components/McpPanel';
import {SchedulerPanel} from './components/SchedulerPanel';
import {ModelControls} from './components/ModelControls';
import {ComfyPanel} from './components/ComfyPanel';
import {HostVramDropCard, ModelDragProvider} from './components/modelDrag';
import {AddHost} from './components/AddHost';
import {HostAuthControl, HostPasswordSettingsDialog, HostUnlockDialog} from './components/HostAuthDialogs';
import {JobsPanel, Progress} from './components/JobsPanel';
import {Badge, Empty, Skeleton} from './components/ui';
import './style.css';
import '../../tokens.css';
import './console.css';

function App() {
  const { locale, setLocale, t, formatTime, formatNowClock, blockerLabel, translateApiText } = useI18n();
  const [page, setPage] = useState('overview');
  const [data, setData] = useState<{nodes:Node[]; jobs:Job[]; models:Model[]; overview:Overview} | null>(null);
  const [error,setError] = useState('');
  const [actionError,setActionError] = useState('');
  const [updated,setUpdated] = useState('');
  const [creating,setCreating] = useState(false);
  const [addingHost,setAddingHost] = useState(false);
  const [unlockTarget,setUnlockTarget] = useState<{node:Node; pendingAction?:{label:string; run:()=>Promise<void>}} | null>(null);
  const [passwordTarget,setPasswordTarget] = useState<Node | null>(null);
  const [selected,setSelected] = useState<string|null>(null);
  const [detail,setDetail] = useState<Job|null>(null);
  const [explanation,setExplanation] = useState<Explanation|null>(null);
  const [filter,setFilter] = useState('all');
  const [query,setQuery] = useState('');
  const [busy,setBusy] = useState(false);
  const [manualRefresh,setManualRefresh] = useState(false);
  const refreshing = useRef(false);

  const pages = [
    {id:'overview', eyebrow:t('INFRASTRUCTURE · 運算總覽', 'INFRASTRUCTURE · OVERVIEW'), title:t('運算總覽', 'Compute Overview'), short:t('總覽', 'Overview'), icon:Gauge, description:t('掌握硬體負載與任務狀態，安排下一次推論。', 'Monitor live GPU telemetry, host availability, and active inference workloads.')},
    {id:'jobs', eyebrow:t('SCHEDULER · 任務佇列', 'SCHEDULER · QUEUE'), title:t('任務佇列', 'Job Queue'), short:t('任務', 'Jobs'), icon:Queue, description:t('追蹤每個應用的工作，從排隊到生成完成。', 'Track inference and model control jobs from queue dispatch to completion.')},
    {id:'scheduler', eyebrow:t('ALGORITHM · 動態排程與路由', 'ALGORITHM · DYNAMIC SCHEDULER'), title:t('動態排程', 'Dynamic Scheduler'), short:t('排程', 'Scheduler'), icon:SlidersHorizontal, description:t('依據多服務公平權重、SLA 優先級、熱模型親和與 GPU 即時負載，動態決定任務路由與保護動作。', 'Dynamically route multi-service inference requests using fair-share weights, SLA urgency, warm affinity, and live GPU health.')},
    {id:'nodes', eyebrow:t('HARDWARE · 運算主機', 'HARDWARE · NODES'), title:t('運算主機', 'Compute Hosts'), short:t('主機', 'Hosts'), icon:DesktopTower, description:t('查看實際硬體數據、心跳與接單狀態。', 'Inspect real-time GPU metrics, host heartbeats, and scheduling readiness.')},
    {id:'models', eyebrow:t('RUNTIMES · 模型與服務', 'RUNTIMES · MODELS'), title:t('模型與服務', 'Models & Services'), short:t('模型', 'Models'), icon:Stack, description:t('切換主機預設執行器、管理模型載入與 GPU 落點，並檢視 ComfyUI 服務狀態。', 'Switch host default runtimes, pin models to GPUs, and inspect ComfyUI service status.')},
    {id:'energy', eyebrow:t('ANALYTICS · 電費與能耗', 'ANALYTICS · ENERGY'), title:t('電費分析', 'Energy & Cost'), short:t('電費', 'Energy'), icon:Lightning, description:t('即時紀錄 GPU 功耗、尖離峰時間電價核算、統計分位數與帳單對帳分析。', 'Track live GPU power telemetry, Time-of-Use cost, statistical load profiles, and bill reconciliation.')},
    {id:'hardware-history', eyebrow:t('DIAGNOSTICS · 硬體追蹤', 'DIAGNOSTICS · TELEMETRY'), title:t('硬體追蹤', 'Hardware History'), short:t('追蹤', 'History'), icon:Pulse, description:t('後台長期記錄 GPU 溫度、風扇、顯存、時脈與主機資源走勢，並自動追蹤異常告警與維運日誌。', 'Track historical GPU thermals, fan curves, VRAM, clocks, host vitals, anomaly alerts, and ops logs.')},
    {id:'mcp', eyebrow:t('INTEGRATION · 外部 AGENT 接入', 'INTEGRATION · MCP HUB'), title:t('MCP 接入', 'MCP Hub'), short:t('MCP', 'MCP'), icon:PlugsConnected, description:t('發行分級 API Token，讓 Claude Code、Cursor、Codex、Python 與其他 Agent 系統直接共用本平台 GPU。', 'Issue scoped API tokens and copy ready-to-use configs so external agent systems can share this GPU fleet via MCP.')},
  ];

  const refresh = useCallback(async () => {
    if(refreshing.current) return;
    refreshing.current = true;
    try {
      const [nodes,jobs,models,overview] = await Promise.all([api<Node[]>('/nodes'),api<Job[]>('/jobs'),api<Model[]>('/models'),api<Overview>('/overview')]);
      setData({nodes,jobs,models,overview}); setError('');
      setUpdated(formatNowClock());
    } catch(e) {setError(e instanceof Error ? translateApiText(e.message) : t('無法連線到中央服務', 'Unable to connect to the central API'));}
    finally {refreshing.current = false;}
  },[formatNowClock, t, translateApiText]);
  useEffect(() => {void refresh(); const id=setInterval(() => void refresh(),2000); return () => clearInterval(id);},[refresh]);
  useEffect(() => {
    if(!selected) {setDetail(null); setExplanation(null); return;}
    let current=true;
    setDetail(null); setExplanation(null);
    const load=async () => {
      try {
        const [result,why]=await Promise.all([api<Job>(`/jobs/${selected}`),api<Explanation>(`/jobs/${selected}/explain`)]);
        if(current) {setDetail(result);setExplanation(why);}
      } catch(e) {if(current) setActionError(e instanceof Error ? translateApiText(e.message) : t('讀取失敗', 'Failed to load job details'));}
    };
    void load();const id=setInterval(() => void load(),2000);
    return () => {current=false;clearInterval(id);};
  },[selected, t, translateApiText]);
  async function action(path:string,method='POST',body?:unknown) {
    setBusy(true);setActionError('');
    try {
      const result=await api<Job>(path,method,body);await refresh();
      if(path.endsWith('/retry')) setSelected(result.id);
      else if(selected && path.startsWith(`/jobs/${selected}/`)) setDetail(await api<Job>(`/jobs/${selected}`));
    } catch(e) {setActionError(e instanceof Error ? translateApiText(e.message) : t('操作失敗', 'Action failed'));}
    finally {setBusy(false);}
  }
  const currentPage=pages.find(p => p.id===page)!;
  const counts=data?.overview.jobs || {};
  const availableModels=data?.models.filter(m => m.available).length || 0;
  const gpuCount=data?.nodes.reduce((sum,n) => sum+(n.metrics.gpus?.length || 0),0) || 0;
  const liveGpuWatts=data?.nodes.reduce((sum,n) => n.status==='offline' || n.mode==='demo' ? sum : sum+(n.metrics.gpus || []).reduce((gs,g) => gs+(g.power_w && Number.isFinite(g.power_w) ? g.power_w : 0),0),0) || 0;
  function navigate(id:string) {setPage(id);setSelected(null);setActionError('');}
  function openJob(id:string) {setSelected(id);setActionError('');}
  const describeModelAction = (node:Node, model:string, operation:'load'|'unload', gpuIndices?:number[]|null, replace?:boolean) => {
    if (model === 'comfyui:*' && operation === 'unload') {
      return t('釋放 ComfyUI 顯存快取', 'Clear ComfyUI VRAM cache');
    }
    const rawShort = model.includes(':') ? model.split(':', 2)[1] : model;
    const display = modelDisplayName(node, model, rawShort);
    const gpuSuffix = gpuIndices?.length
      ? ` (${gpuIndices.map(i => `GPU ${String(i).padStart(2, '0')}`).join(', ')})`
      : '';
    if (operation === 'unload') return t(`卸載 ${display}`, `Unload ${display}`);
    if (replace) return t(`切換載入 ${display}${gpuSuffix}`, `Reload ${display}${gpuSuffix}`);
    return t(`載入 ${display}${gpuSuffix}`, `Load ${display}${gpuSuffix}`);
  };
  const lockHost = (node:Node) => {
    void action(`/nodes/${node.id}/auth/lock`, 'POST');
  };
  const triggerModelAction = (node:Node, model:string, operation:'load'|'unload', gpuIndices?:number[]|null, replace?:boolean) => {
    const payload: Record<string, unknown> = {model, action: operation};
    if (gpuIndices !== undefined && gpuIndices !== null) payload.gpu_indices = gpuIndices;
    if (replace) payload.replace = true;
    const label = describeModelAction(node, model, operation, gpuIndices, replace);
    const runAction = async () => {
      setBusy(true); setActionError('');
      try {
        await api<Job>(`/nodes/${node.id}/model-actions`, 'POST', payload);
        await refresh();
      } catch (e) {
        if (e instanceof ApiError && e.authRequired) {
          setUnlockTarget({node, pendingAction: {label, run: runAction}});
          return;
        }
        setActionError(e instanceof Error ? translateApiText(e.message) : t('操作失敗', 'Action failed'));
      } finally {
        setBusy(false);
      }
    };
    if (node.auth_required && !node.auth_unlocked) {
      setUnlockTarget({node, pendingAction: {label, run: runAction}});
      return;
    }
    void runAction();
  };
  const renameModel = (node:Node, model:string, alias:string) => {
    void action(`/nodes/${node.id}/model-aliases`, 'PATCH', {model, alias});
  };
  const updateHiddenModels = (node:Node, hide:string[], unhide:string[]) => {
    void action(`/nodes/${node.id}/hidden-models`, 'PATCH', {hide, unhide});
  };
  const jobProps={jobs:data?.jobs || [],filter,query,setFilter,setQuery,onOpen:openJob,onAll:() => navigate('jobs'),onCreate:() => setCreating(true)};
  const runningCount = (counts.running || 0) + (counts.cancelling || 0);
  return <ModelDragProvider jobs={data?.jobs || []} busy={busy} onModelAction={triggerModelAction}><div className="gcc gcc-shell" data-locale={locale}>
    <a className="gcc-skip" href="#main-content">{t('跳至主要內容', 'Skip to main content')}</a>
    <aside className="gcc-sidebar">
      <a className="gcc-brand" href="#" onClick={e => {e.preventDefault();navigate('overview');}} aria-label={t('GPU Control Center 首頁', 'GPU Control Center Home')}><span className="gcc-brand-symbol"><Cpu size={24} weight="regular" /></span><span>GPU<span className="gcc-brand-name">Control Center</span></span></a>
      <div className="gcc-workspace"><span className="gcc-workspace-mark"><DesktopTower size={17} /></span><div><strong>{t('本機工作區', 'Local Workspace')}</strong><span>Local Infrastructure</span></div></div>
      <p className="gcc-nav-label">{t('工作空間', 'Workspace')}</p>
      <nav className="gcc-nav" aria-label={t('主導覽', 'Primary navigation')}>{pages.map(p => <button key={p.id} aria-current={page===p.id ? 'page' : undefined} className={`gcc-nav-link${page===p.id ? ' is-active' : ''}`} onClick={() => navigate(p.id)}><p.icon size={20} /><span className="gcc-nav-full">{p.title}</span><span className="gcc-nav-short">{p.short}</span>{p.id==='jobs' && !!counts.queued && <b className="gcc-nav-count">{counts.queued}</b>}</button>)}</nav>
      <div className="gcc-sidebar-resource"><span>{t('已連線資源', 'Connected Fleet')}</span><div><strong>{data ? gpuCount : '—'}</strong><span>{t('GPU 裝置', gpuCount === 1 ? 'GPU' : 'GPUs')}</span></div><small>{data ? data.nodes.length : '—'} {t('台主機', data?.nodes.length === 1 ? 'host' : 'hosts')} · {availableModels} {t('個可用模型', availableModels === 1 ? 'model' : 'models')}</small>{liveGpuWatts > 0 && <button type="button" className="gcc-sidebar-power-pill" onClick={() => navigate('energy')}><Lightning size={13} /><span>{t('即時 GPU 功耗', 'Live GPU Power')}</span><strong>{liveGpuWatts.toFixed(0)} W</strong></button>}</div>
      <div className="gcc-sidebar-bottom"><GitBranch size={17} /><span>{t('本機部署', 'Local Deployment')}<span>LM Studio · Ollama · ComfyUI</span></span></div>
    </aside>
    <div className="gcc-work-area">
      <header className="gcc-topbar">
        <div className="gcc-breadcrumb"><span>{t('工作空間', 'Workspace')}</span><span aria-hidden="true">/</span><strong>{currentPage.title}</strong></div>
        <div className="gcc-topbar-actions">
          <div className="gcc-lang-switch" role="group" aria-label={t('切換顯示語言', 'Switch display language')}>
            <button type="button" className={`gcc-lang-btn${locale === 'zh-TW' ? ' is-active' : ''}`} aria-pressed={locale === 'zh-TW'} onClick={() => setLocale('zh-TW')}>繁中</button>
            <button type="button" className={`gcc-lang-btn${locale === 'en' ? ' is-active' : ''}`} aria-pressed={locale === 'en'} onClick={() => setLocale('en')}>EN</button>
          </div>
          <span className={`gcc-live${error ? ' is-offline' : ''}`}><span aria-hidden="true" />{error ? t('連線中斷', 'Disconnected') : data ? t('即時同步', 'Live Sync') : t('連線中', 'Connecting')}</span>
          <button className="gcc-icon-button" aria-label={t('重新整理資料', 'Refresh data')} title={t('重新整理', 'Refresh')} disabled={manualRefresh} onClick={async () => {setManualRefresh(true);await refresh();setManualRefresh(false);}}><ArrowClockwise size={18} className={manualRefresh ? 'gcc-spin' : ''} /></button>
          <a className="gcc-doc-link" href="/docs" target="_blank" rel="noreferrer"><BookOpen size={17} /><span>{t('API 文件', 'API Docs')}</span></a>
        </div>
      </header>
      <main id="main-content" className="gcc-content" tabIndex={-1}>
        <div className="gcc-page-head">
          <div>
            <span className="gcc-page-eyebrow">{currentPage.eyebrow}</span>
            <h1>{currentPage.title}</h1>
            <p>{currentPage.description}</p>
          </div>
          <div className="gcc-page-actions">
            {(page==='nodes' || page==='overview') && <button className={`gcc-button${page==='nodes' ? ' gcc-button--primary' : ''}`} disabled={!data || !!error} onClick={() => setAddingHost(true)}><Plus size={18}/>{t('新增主機', 'Add Host')}</button>}
            {page!=='energy' && page!=='hardware-history' && page!=='mcp' && <button className={`gcc-button${page==='nodes' ? '' : ' gcc-button--primary'}`} onClick={() => setCreating(true)}><Plus size={18} />{t('建立任務', 'New Job')}</button>}
          </div>
        </div>
        {error && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><div><strong>{t('暫時無法更新資料', 'Temporarily unable to refresh data')}</strong><p>{error} {data ? t('目前顯示最後一次收到的資料，正在自動重連。', 'Showing last received snapshot while reconnecting automatically.') : t('正在自動重連。', 'Reconnecting automatically.')}</p></div><button className="gcc-button gcc-button--quiet" onClick={() => void refresh()}>{t('重試', 'Retry')}</button></div>}
        {actionError && !selected && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><p>{actionError}</p><button className="gcc-icon-button" aria-label={t('關閉錯誤訊息', 'Dismiss error')} onClick={() => setActionError('')}><X size={18} /></button></div>}
        {!data && !error && <Skeleton rows={5} />}
        {data && <>
          {page==='overview' && <>
            <section className="gcc-summary" aria-label={t('平台統計', 'Platform summary')}>{[
              {id:'online', label:t('在線主機', 'Online Hosts'), value:data.overview.online_nodes, unit:t('台', data.overview.online_nodes === 1 ? 'host' : 'hosts'), icon:DesktopTower},
              {id:'running', label:t('執行中', 'Running'), value:runningCount, unit:t('個任務', runningCount === 1 ? 'job' : 'jobs'), icon:Pulse},
              {id:'queued', label:t('等待排程', 'Queued'), value:counts.queued || 0, unit:t('個任務', (counts.queued || 0) === 1 ? 'job' : 'jobs'), icon:Queue},
              {id:'completed', label:t('累計完成', 'Completed'), value:counts.completed || 0, unit:t('個任務', (counts.completed || 0) === 1 ? 'job' : 'jobs'), icon:Check},
            ].map(stat => <div className={`gcc-stat gcc-stat--${stat.id}${stat.value > 0 ? ' is-highlighted' : ''}`} key={stat.id}><span><span className="gcc-stat-icon" aria-hidden="true"><stat.icon size={15} /></span>{stat.label}</span><div><strong>{stat.value}</strong><span>{stat.unit}</span></div></div>)}</section>
            {!!counts.unknown && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><p>{t(`${counts.unknown} 個任務尚未確認停止，已保留原主機工作槽。`, `${counts.unknown} job(s) have unconfirmed termination; host execution slot remains reserved.`)}</p><button className="gcc-button gcc-button--quiet" onClick={() => {navigate('jobs');setFilter('unknown');}}>{t('查看任務', 'Inspect Jobs')}</button></div>}
            <div className="gcc-overview-grid"><HardwarePanel nodes={data.nodes} jobs={data.jobs} onModelAction={triggerModelAction} onRename={renameModel} onHiddenChange={updateHiddenModels} onManageModels={() => navigate('models')} onUnlockHost={node => setUnlockTarget({node})} onLockHost={lockHost} onManageHostPassword={node => setPasswordTarget(node)} busy={busy} onMaintenance={node => void action(`/nodes/${node.id}/maintenance`,'PATCH',{enabled:!node.maintenance})} /><SlotPanel nodes={data.nodes} jobs={data.jobs} /></div>
            <JobsPanel {...jobProps} compact />
          </>}
          {page==='jobs' && <JobsPanel {...jobProps} />}
          {page==='scheduler' && <SchedulerPanel nodes={data.nodes} models={data.models} onOpenJob={openJob} />}
          {page==='nodes' && <HardwarePanel nodes={data.nodes} jobs={data.jobs} onModelAction={triggerModelAction} onRename={renameModel} onHiddenChange={updateHiddenModels} onManageModels={() => navigate('models')} onUnlockHost={node => setUnlockTarget({node})} onLockHost={lockHost} onManageHostPassword={node => setPasswordTarget(node)} busy={busy} onMaintenance={node => void action(`/nodes/${node.id}/maintenance`,'PATCH',{enabled:!node.maintenance})} />}
          {page==='models' && <section className="gcc-model-workspace" aria-label={t('各主機模型與服務', 'Host models and services')}>
            {data.nodes.length ? data.nodes.map(node => {
              const gpus = node.metrics.gpus || [];
              const totalVramMb = gpus.reduce((s,g)=>s+(g.memory_total_mb ?? 0),0);
              const usedVramMb = gpus.reduce((s,g)=>s+(g.memory_used_mb ?? 0),0);
              const freeVramGb = totalVramMb > 0 ? Math.max(0, (totalVramMb - usedVramMb) / 1024).toFixed(1) : null;
              const totalVramGb = totalVramMb > 0 ? (totalVramMb / 1024).toFixed(0) : null;
              const loadedTotal = (node.metrics.lmstudio?.loaded_models?.length || 0) + (node.metrics.ollama?.loaded_models?.length || 0);
              return <article className="gcc-model-host" key={node.id}>
                <header className="gcc-model-host-head">
                  <div className="gcc-host-name">
                    <span className="gcc-host-symbol"><DesktopTower size={20}/></span>
                    <div>
                      <h2>{node.name}</h2>
                      <p>{node.mode==='demo' ? t('模擬主機', 'Simulated Host') : node.connection_host ? t('遠端服務主機', 'Remote Service Host') : t('本機運算主機', 'Local Compute Host')} · {gpus.length} {t('張顯示卡', gpus.length === 1 ? 'GPU' : 'GPUs')}</p>
                    </div>
                  </div>
                  <div className="gcc-model-host-stats">
                    {freeVramGb !== null && <span className="gcc-host-kpi"><em>{t('可用顯存', 'Free VRAM')}</em><strong>{freeVramGb} / {totalVramGb} GB</strong></span>}
                    <span className="gcc-host-kpi"><em>{t('常駐模型', 'Loaded')}</em><strong>{loadedTotal}</strong></span>
                    <HostAuthControl node={node} busy={busy} onUnlock={n => setUnlockTarget({node: n})} onLock={lockHost} onManagePassword={n => setPasswordTarget(n)} />
                    {node.maintenance && <Badge value="maintenance"/>}
                    <Badge value={node.status}/>
                  </div>
                </header>
                <div className="gcc-model-host-grid">
                  <div className="gcc-model-host-main">
                    <ModelControls node={node} jobs={data.jobs} busy={busy} onAction={triggerModelAction} onPreference={(n,runtime)=>void action(`/nodes/${n.id}/default-runtime`,'PATCH',{runtime})} onRename={renameModel} onHiddenChange={updateHiddenModels} onUnlockHost={n => setUnlockTarget({node: n})}/>
                  </div>
                  <aside className="gcc-model-host-aside" aria-label={t(`${node.name} 顯存與關聯服務`, `${node.name} VRAM and connected services`)}>
                    <HostVramDropCard node={node} jobs={data.jobs} busy={busy}/>
                    <ComfyPanel node={node} jobs={data.jobs} busy={busy} onAction={triggerModelAction} onRename={renameModel} onHiddenChange={updateHiddenModels}/>
                  </aside>
                </div>
              </article>;
            }) : <Empty icon={<Stack size={28}/>} title={t('等待主機連線', 'Waiting for Hosts')}>{t('啟動主機 Agent 後即可管理模型。', 'Start a host agent to manage models and runtimes.')}</Empty>}
          </section>}
          {page==='energy' && <EnergyPanel nodes={data.nodes} />}
          {page==='mcp' && <McpPanel />}
          {page==='hardware-history' && <HardwareHistoryPanel nodes={data.nodes} />}
          <footer className="gcc-footer"><span><span className="gcc-keyline" />{t('中央排程 · 原生 Agent · 真實推論', 'Central Scheduler · Native Agent · Real Inference')}</span><span>{error ? t('最後成功同步', 'Last successful sync') : t('最後同步', 'Last synced')} <time>{updated}</time></span></footer>
        </>}
      </main>
    </div>
    {addingHost && <AddHost nodes={data?.nodes || []} onAdded={refresh} onClose={() => setAddingHost(false)}/>}
    {unlockTarget && <HostUnlockDialog node={unlockTarget.node} pendingLabel={unlockTarget.pendingAction?.label} onClose={() => setUnlockTarget(null)} onUnlocked={async () => {const pending = unlockTarget.pendingAction; setUnlockTarget(null); await refresh(); if (pending) await pending.run();}}/>}
    {passwordTarget && <HostPasswordSettingsDialog node={passwordTarget} onClose={() => setPasswordTarget(null)} onUpdated={refresh}/>}
    {creating && <CreateJob nodes={data?.nodes || []} models={(data?.models || []).filter(m=>!m.hidden && (!m.model_type || m.model_type==='llm')).sort((a,b)=>Number(b.runtime===data?.nodes[0]?.default_runtime)-Number(a.runtime===data?.nodes[0]?.default_runtime))} onClose={() => setCreating(false)} onCreated={async id => {setCreating(false);openJob(id);await refresh();}} />}
    {selected && <DetailPanel onClose={() => setSelected(null)}><div className="gcc-dialog-head"><span>{t('任務詳情', 'Job Details')}</span><button className="gcc-icon-button" aria-label={t('關閉任務詳情', 'Close job details')} onClick={() => setSelected(null)}><X size={21} /></button></div>{detail ? <JobDetails detail={detail} explanation={explanation} busy={busy} error={actionError} onAction={path => void action(path)} /> : actionError ? <div className="gcc-dialog-loading gcc-alert" role="alert">{actionError}</div> : <Skeleton rows={4} />}</DetailPanel>}
  </div></ModelDragProvider>;
}

function CopyButton({text,label}:{text:string;label?:string}) {
  const { t } = useI18n();
  const [copied,setCopied]=useState(false);
  const [failed,setFailed]=useState(false);
  useEffect(() => {if(!copied) return;const id=setTimeout(() => setCopied(false),2000);return () => clearTimeout(id);},[copied]);
  return <button className="gcc-button gcc-button--quiet" onClick={async () => {try {await navigator.clipboard.writeText(text);setCopied(true);setFailed(false);} catch {setFailed(true);}}}>{copied ? <Check size={16} /> : <Copy size={16} />}{copied ? t('已複製', 'Copied') : failed ? t('複製失敗，請手動選取', 'Copy failed — select manually') : (label || t('複製內容', 'Copy Output'))}</button>;
}

function JobDetails({detail,explanation,busy,error,onAction}:{detail:Job;explanation:Explanation|null;busy:boolean;error:string;onAction:(path:string)=>void}) {
  const { t, formatTime, blockerLabel, translateApiText } = useI18n();
  const control=!!detail.parameters.model_action;
  const real=!control && (detail.model.startsWith('ollama:') || detail.model.startsWith('lmstudio:'));
  const output=detail.runtime_state.text || (typeof detail.result?.text==='string' ? detail.result.text : '');
  return <>
    <div className="gcc-detail-title"><div><h2 id="detail-title">{detail.name}</h2><p><code>{detail.id.slice(0,8)}</code><span>{t(`來自 ${detail.source}`, `from ${detail.source}`)}</span></p></div><Badge value={detail.status} /></div>
    {error && <div className="gcc-detail-error gcc-alert" role="alert"><WarningCircle size={20} /><p>{error}</p></div>}
    <div className="gcc-detail-grid"><div className="gcc-detail-main">
      {real && <><section className="gcc-output-section"><div className="gcc-output-head"><h3>{t('生成內容', 'Generated Output')}</h3>{output && <CopyButton text={output} />}</div><div className={`gcc-output${output ? '' : ' is-empty'}`} aria-label={t('生成內容', 'Generated output')}>{output || (detail.status==='queued' ? t('等待中央派送後開始生成。', 'Waiting for central dispatch to begin generation.') : detail.status==='cancelled' ? t('任務已取消。', 'Job was cancelled.') : detail.status==='failed' ? t('本次推論未產生內容。', 'No output was generated for this run.') : t('模型準備中，文字會顯示在這裡。', 'Preparing model — generated text will stream here.'))}</div><div className="gcc-output-meta"><Progress job={detail} />{typeof detail.result?.tokens_per_second==='number' && <span>{detail.result.tokens_per_second.toFixed(1)} tokens/s</span>}</div></section><section className="gcc-prompt-section"><h3>{t('提示詞', 'Prompt')}</h3><p>{detail.parameters.prompt}</p></section></>}
      {control && <div className="gcc-inline-note"><Stack size={22}/><div><h3>{t('模型操作', 'Model Control Operation')}</h3><p>{detail.name}</p><Progress job={detail}/><p>{t('由指定主機執行，不會產生文字內容。操作送出後需等待執行器確認。', 'Executed on the target host without text generation. Awaiting runtime confirmation.')}</p></div></div>}
      {!real && !control && <div className="gcc-inline-note"><Info size={18} /><p>{t('這是模擬工作，沒有執行 AI 推論。', 'This is a simulated job — no AI inference was executed.')}</p></div>}
      <details className="gcc-disclosure" open={['queued','unknown','cancelling','failed'].includes(detail.status)}><summary><ListBullets size={18} />{t('排程說明', 'Scheduling Explanation')}</summary><div>{explanation ? <><p>{explanation.summary}</p>{explanation.queue_rank !== null && <p className="gcc-muted">{t(`目前排序 ${explanation.queue_rank} / ${explanation.queue_size} · 策略 ${explanation.policy_mode || 'dynamic'} · SLA ${explanation.sla_class || 'standard'}`, `Queue rank ${explanation.queue_rank} / ${explanation.queue_size} · Policy ${explanation.policy_mode || 'dynamic'} · SLA ${explanation.sla_class || 'standard'}`)}</p>}{detail.status==='queued' && explanation.candidates.map(n => <div className="gcc-candidate" key={n.node_id}><div className="gcc-sched-cand-head"><strong>{n.name}</strong>{n.recommended && <span className="gcc-sched-pill is-dispatch">{t('推薦主機', 'Recommended')}</span>}{n.warm_hit && <span className="gcc-sched-pill is-yield">{t('熱命中', 'Warm')}</span>}{n.score !== undefined && <code className="gcc-muted">Score {n.score.toFixed(2)}</code>}</div><p>{n.ready ? t('可接單，等待 Agent 輪詢', 'Ready — awaiting agent poll') : n.blockers.map(b => blockerLabel(b)).join(t('、', ', '))}</p>{n.score_breakdown && <div className="gcc-sched-breakdown">{Object.entries(n.score_breakdown).filter(([k,v])=>k!=='total' && Math.abs(v)>=0.05).map(([k,v])=><span key={k}>{k}: {v>=0?`+${v.toFixed(1)}`:v.toFixed(1)}</span>)}</div>}{n.holders.map(h => <p className="gcc-muted" key={h.id}>{t(`目前持有者：${h.source} · ${h.name}`, `Current holder: ${h.source} · ${h.name}`)}</p>)}</div>)}</> : <p className="gcc-muted">{t('正在讀取排程狀態…', 'Loading scheduler state…')}</p>}</div></details>
      <details className="gcc-disclosure"><summary><Pulse size={18} />{t('執行紀錄', 'Execution Timeline')} <span>{detail.events?.length || 0}</span></summary><ol className="gcc-timeline">{detail.events?.map(e => <li key={e.id}><time dateTime={e.created_at}>{formatTime(e.created_at)}</time><p>{translateApiText(e.message)}</p></li>)}</ol></details>
      {detail.result && <details className="gcc-disclosure gcc-raw"><summary><GitBranch size={18} />{t('原始回應', 'Raw Response')}</summary><pre>{JSON.stringify(detail.result,null,2)}</pre></details>}
    </div><aside className="gcc-detail-aside"><h3>{t('執行資訊', 'Execution Info')}</h3><dl><dt>{t('模型', 'Model')}</dt><dd>{detail.model.replace(/^(ollama|lmstudio|comfyui):/,'')}</dd>{detail.parameters.original_model && <><dt>{t('原請求模型', 'Original Model')}</dt><dd>{detail.parameters.original_model.replace(/^(ollama|lmstudio):/,'')}</dd></>}<dt>{t('來源應用', 'Source')}</dt><dd>{detail.source}</dd>{detail.parameters.sla_class && <><dt>SLA</dt><dd>{detail.parameters.sla_class}</dd></>}<dt>{t('執行主機', 'Host')}</dt><dd>{detail.node_id || t('等待分配', 'Pending assignment')}</dd>{detail.parameters.gpu_indices?.length ? <><dt>{t('指定 GPU', 'Target GPU')}</dt><dd>{detail.parameters.gpu_indices.map(i=>`GPU ${String(i).padStart(2,'0')}`).join(', ')}</dd></> : null}<dt>{t('優先級', 'Priority')}</dt><dd>{detail.priority}</dd>{detail.parameters.deadline_seconds ? <><dt>{t('截止秒數', 'Deadline')}</dt><dd>{detail.parameters.deadline_seconds}s</dd></> : null}<dt>{t('建立時間', 'Created')}</dt><dd>{formatTime(detail.created_at)}</dd>{detail.finished_at && <><dt>{t('完成時間', 'Finished')}</dt><dd>{formatTime(detail.finished_at)}</dd></>}</dl>
      {detail.error && <div className="gcc-alert"><WarningCircle size={18} /><p>{translateApiText(detail.error)}</p></div>}
      {(['queued','running','unknown'].includes(detail.status) && (!control || detail.status==='queued')) && <button className="gcc-button gcc-button--danger" disabled={busy} onClick={() => onAction(`/jobs/${detail.id}/cancel`)}>{busy ? <CircleNotch size={17} className="gcc-spin" /> : <X size={17} />}{t('取消任務', 'Cancel Job')}</button>}
      {!control && ['failed','cancelled'].includes(detail.status) && <button className="gcc-button" disabled={busy} onClick={() => onAction(`/jobs/${detail.id}/retry`)}><ArrowClockwise size={17} />{t('建立重試任務', 'Retry Job')}</button>}
      {detail.status==='cancelling' && <p className="gcc-cancel-note" role="status">{t('已請求取消，等待本次生成結束並確認停止。', 'Cancellation requested — awaiting confirmation of stop.')}</p>}
      {real && <p className="gcc-detail-note"><Info size={16} />{t('生成長度不固定。完成後會回報實際 token 數。', 'Output length varies by model; actual token count is reported upon completion.')}</p>}
    </aside></div>
  </>;
}

function DetailPanel({onClose,children}:{onClose:()=>void;children:React.ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(() => {const dialog=ref.current;dialog?.showModal();return () => dialog?.close();},[]);
  return <dialog ref={ref} className="gcc-dialog gcc-detail-dialog" aria-labelledby="detail-title" onCancel={onClose} onClick={e => {if(e.target===e.currentTarget) {const box=e.currentTarget.getBoundingClientRect();if(e.clientX<box.left || e.clientX>box.right || e.clientY<box.top || e.clientY>box.bottom) onClose();}}}>{children}</dialog>;
}

function CreateJob({nodes,models,onClose,onCreated}:{nodes:Node[];models:Model[];onClose:()=>void;onCreated:(id:string)=>Promise<void>}) {
  const { t, translateApiText } = useI18n();
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [model,setModel]=useState(models.find(m => m.available)?.id || models[0]?.id || '');
  const [name,setName]=useState(() => t('文字推論', 'Text Inference'));
  const [source,setSource]=useState('console');
  const [prompt,setPrompt]=useState('');
  const [maxTokens,setMaxTokens]=useState('256');
  const [priority,setPriority]=useState('0');
  const [slaClass,setSlaClass]=useState<SlaClass | ''>('');
  const [deadlineSeconds,setDeadlineSeconds]=useState('');
  const [preferredNodeId,setPreferredNodeId]=useState('');
  const [fallbackModel,setFallbackModel]=useState('');
  const [steps,setSteps]=useState('20');
  const [promptTouched,setPromptTouched]=useState(false);
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(() => {const dialog=ref.current;dialog?.showModal();return () => dialog?.close();},[]);
  const demo=model==='demo.progress';
  const promptError=!demo && promptTouched && !prompt.trim() ? t('請輸入提示詞，模型才能開始生成。', 'Enter a prompt so the model can start generating.') : '';
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setPromptTouched(true);
    if(!demo && !prompt.trim()) {ref.current?.querySelector('textarea')?.focus();return;}
    setBusy(true);setError('');
    try {
      const payload: Record<string, unknown> = {
        name:name.trim(),
        source:source.trim(),
        model,
        prompt:demo ? '' : prompt,
        max_tokens:demo ? 256 : Number(maxTokens),
        steps:demo ? Number(steps) : 20,
        priority:Number(priority),
      };
      if (slaClass) payload.sla_class = slaClass;
      if (deadlineSeconds.trim()) payload.deadline_seconds = Number(deadlineSeconds);
      if (preferredNodeId) payload.preferred_node_id = preferredNodeId;
      if (fallbackModel && fallbackModel !== model) payload.fallback_models = [fallbackModel];
      const job=await api<Job>('/jobs','POST',payload);
      await onCreated(job.id);
    }
    catch(e) {setError(e instanceof Error ? translateApiText(e.message) : t('建立失敗，請稍後重試', 'Failed to create job, please try again'));setBusy(false);}
  }
  return <dialog ref={ref} className="gcc-dialog gcc-create-dialog" aria-labelledby="create-title" onCancel={e => {if(busy) e.preventDefault();else onClose();}}><form onSubmit={e => void submit(e)}>
    <div className="gcc-dialog-head"><span><Plus size={18} />{t('新增工作', 'New Workload')}</span><button type="button" className="gcc-icon-button" aria-label={t('關閉建立任務', 'Close create job dialog')} onClick={onClose} disabled={busy}><X size={21} /></button></div>
    <div className="gcc-create-body"><h2 id="create-title">{t('建立推論任務', 'Create Inference Job')}</h2><p className="gcc-create-description">{t('選擇模型並輸入內容，由中央安排執行。', 'Select a model and prompt to dispatch through the central scheduler.')}</p>
      <label className="gcc-field"><span>{t('模型', 'Model')} <small>{t('必填', 'Required')}</small></span><select value={model} onChange={e => setModel(e.target.value)} required disabled={busy}><option value="" disabled>{t('選擇已安裝模型', 'Select an installed model')}</option>{models.map(m => <option key={m.id} value={m.id}>{m.runtime==='lmstudio' ? 'LM Studio' : m.runtime==='ollama' ? 'Ollama' : 'Demo'} · {m.display_name || m.name}{m.available ? '' : t('（離線，可排隊）', ' (Offline, queueable)')}</option>)}</select><span className="gcc-field-help">{models.length ? t('使用所選執行器上已安裝的文字模型。', 'Uses installed text models from the selected runtime.') : t('尚無模型。請確認 Agent 已連線，並在執行器中安裝模型。', 'No models found. Ensure the host agent is online with models installed.')}</span></label>
      {!demo && <label className="gcc-field"><span>{t('提示詞', 'Prompt')} <small>{t('必填', 'Required')}</small></span><textarea name="prompt" rows={6} value={prompt} onChange={e => setPrompt(e.target.value)} onBlur={() => setPromptTouched(true)} autoFocus required aria-invalid={!!promptError} aria-describedby="prompt-help" maxLength={32000} placeholder={t('例如：請用三點說明 GPU 排程的用途。', 'e.g. Summarize the benefits of centralized GPU scheduling in 3 bullet points.')} /><span id="prompt-help" className={`gcc-field-help${promptError ? ' is-error' : ''}`}>{promptError || t('文字會逐步顯示在任務詳情中。', 'Generated text streams live in the job details panel.')}</span></label>}
      <label className="gcc-field gcc-token-field"><span>{demo ? t('模擬步數', 'Simulated Steps') : t('最大生成 tokens', 'Max Output Tokens')}</span><input type="number" min={demo ? 5 : 1} max={demo ? 120 : 2048} value={demo ? steps : maxTokens} onChange={e => demo ? setSteps(e.target.value) : setMaxTokens(e.target.value)} required disabled={busy} /><span className="gcc-field-help">{demo ? t('每一步約 0.5 秒，不執行 AI 推論。', 'Each step takes ~0.5s with no AI inference.') : t('生成上限 1–2048，實際長度由模型決定。', 'Limit 1–2048; actual length depends on the model.')}</span></label>
      <details className="gcc-disclosure gcc-advanced"><summary>{t('進階排程與路由設定', 'Advanced Scheduling & Routing')}</summary><div className="gcc-form-grid"><label className="gcc-field"><span>{t('任務名稱', 'Job Name')}</span><input value={name} onChange={e => setName(e.target.value)} required maxLength={120} disabled={busy} /></label><label className="gcc-field"><span>{t('來源應用', 'Source App')}</span><input value={source} onChange={e => setSource(e.target.value)} required maxLength={80} disabled={busy} /></label><label className="gcc-field"><span>{t('優先級（0–10）', 'Priority (0–10)')}</span><input type="number" min={0} max={10} value={priority} onChange={e => setPriority(e.target.value)} required disabled={busy} /></label><label className="gcc-field"><span>{t('SLA 等級', 'SLA Class')}</span><select value={slaClass} onChange={e => setSlaClass(e.target.value as SlaClass | '')} disabled={busy}><option value="">{t('標準 (預設)', 'Standard (Default)')}</option><option value="realtime">{t('即時 (realtime)', 'Realtime')}</option><option value="interactive">{t('互動 (interactive)', 'Interactive')}</option><option value="batch">{t('批次 (batch)', 'Batch')}</option><option value="background">{t('背景 (background)', 'Background')}</option></select></label><label className="gcc-field"><span>{t('截止時間（秒，選填）', 'Deadline (s, optional)')}</span><input type="number" min={5} max={86400} placeholder="—" value={deadlineSeconds} onChange={e => setDeadlineSeconds(e.target.value)} disabled={busy} /></label><label className="gcc-field"><span>{t('偏好主機（選填）', 'Preferred Host (opt)')}</span><select value={preferredNodeId} onChange={e => setPreferredNodeId(e.target.value)} disabled={busy}><option value="">{t('交由演算法自動挑選', 'Auto-select by algorithm')}</option>{nodes.map(n => <option key={n.id} value={n.id}>{n.name}</option>)}</select></label><label className="gcc-field"><span>{t('備援模型（選填）', 'Fallback Model (opt)')}</span><select value={fallbackModel} onChange={e => setFallbackModel(e.target.value)} disabled={busy}><option value="">{t('不降級', 'No fallback')}</option>{models.filter(m => m.id !== model).map(m => <option key={m.id} value={m.id}>{m.display_name || m.name}</option>)}</select></label></div></details>
      {error && <div className="gcc-alert" role="alert"><WarningCircle size={19} /><p>{error}</p></div>}
    </div><div className="gcc-create-actions"><span><Lightning size={16} />{demo ? t('模擬執行', 'Simulated Execution') : t('真實推論', 'Real Inference')}</span><div><button type="button" className="gcc-button" onClick={onClose} disabled={busy}>{t('取消', 'Cancel')}</button><button className="gcc-button gcc-button--primary" disabled={busy || !model}>{busy ? <CircleNotch size={17} className="gcc-spin" /> : <Plus size={17} />}{busy ? t('提交中', 'Submitting…') : t('開始推論', 'Start Inference')}</button></div></div>
  </form></dialog>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><I18nProvider><IconContext.Provider value={{weight:"regular", "aria-hidden":true}}><App/></IconContext.Provider></I18nProvider></React.StrictMode>);
