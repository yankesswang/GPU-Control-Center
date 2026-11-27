import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {
  Pulse, IconContext, ArrowClockwise, BookOpen, Check, CircleNotch, Copy, Cpu,
  DesktopTower, Gauge, GitBranch, Info, Lightning, ListBullets, Plus,
  Queue, Stack, WarningCircle, X,
} from '@phosphor-icons/react';
import '@fontsource/ibm-plex-sans/400.css';
import '@fontsource/ibm-plex-sans/500.css';
import '@fontsource/ibm-plex-sans/600.css';
import '@fontsource/ibm-plex-mono/400.css';
import '@fontsource/ibm-plex-mono/500.css';
import {api, type Job, type Model, type Node, type Overview, type Explanation} from './api';
import {HardwarePanel, SlotPanel} from './components/HardwarePanel';
import {JobsPanel, Progress} from './components/JobsPanel';
import {Badge, Empty, Skeleton, time} from './components/ui';
import './style.css';
import '../../tokens.css';
import './console.css';

const pages = [
  {id:'overview', title:'運算總覽', short:'總覽', icon:Gauge, description:'掌握硬體負載與任務狀態，安排下一次推論。'},
  {id:'jobs', title:'任務佇列', short:'任務', icon:Queue, description:'追蹤每個應用的工作，從排隊到生成完成。'},
  {id:'nodes', title:'運算主機', short:'主機', icon:DesktopTower, description:'查看實際硬體數據、心跳與接單狀態。'},
  {id:'models', title:'模型與能力', short:'模型', icon:Stack, description:'使用主機上已安裝的模型，透過中央入口提交工作。'},
];
const blockerLabels: Record<string,string> = {unsupported_capability:'不支援此能力',offline:'主機離線',maintenance:'維護中',unconfirmed_execution:'有執行狀態待確認的工作',slot_occupied:'工作槽已占用'};

function App() {
  const [page, setPage] = useState('overview');
  const [data, setData] = useState<{nodes:Node[]; jobs:Job[]; models:Model[]; overview:Overview} | null>(null);
  const [error,setError] = useState('');
  const [actionError,setActionError] = useState('');
  const [updated,setUpdated] = useState('');
  const [creating,setCreating] = useState(false);
  const [selected,setSelected] = useState<string|null>(null);
  const [detail,setDetail] = useState<Job|null>(null);
  const [explanation,setExplanation] = useState<Explanation|null>(null);
  const [filter,setFilter] = useState('all');
  const [query,setQuery] = useState('');
  const [busy,setBusy] = useState(false);
  const [manualRefresh,setManualRefresh] = useState(false);
  const refreshing = useRef(false);
  const refresh = useCallback(async () => {
    if(refreshing.current) return;
    refreshing.current = true;
    try {
      const [nodes,jobs,models,overview] = await Promise.all([api<Node[]>('/nodes'),api<Job[]>('/jobs'),api<Model[]>('/models'),api<Overview>('/overview')]);
      setData({nodes,jobs,models,overview}); setError('');
      setUpdated(new Date().toLocaleTimeString('zh-TW',{hour12:false}));
    } catch(e) {setError(e instanceof Error ? e.message : '無法連線到中央服務');}
    finally {refreshing.current = false;}
  },[]);
  useEffect(() => {void refresh(); const id=setInterval(() => void refresh(),2000); return () => clearInterval(id);},[refresh]);
  useEffect(() => {
    if(!selected) {setDetail(null); setExplanation(null); return;}
    let current=true;
    setDetail(null); setExplanation(null);
    const load=async () => {
      try {
        const [result,why]=await Promise.all([api<Job>(`/jobs/${selected}`),api<Explanation>(`/jobs/${selected}/explain`)]);
        if(current) {setDetail(result);setExplanation(why);}
      } catch(e) {if(current) setActionError(e instanceof Error ? e.message : '讀取失敗');}
    };
    void load();const id=setInterval(() => void load(),2000);
    return () => {current=false;clearInterval(id);};
  },[selected]);
  async function action(path:string,method='POST',body?:unknown) {
    setBusy(true);setActionError('');
    try {
      const result=await api<Job>(path,method,body);await refresh();
      if(path.endsWith('/retry')) setSelected(result.id);
      else if(selected && path.startsWith(`/jobs/${selected}/`)) setDetail(await api<Job>(`/jobs/${selected}`));
    } catch(e) {setActionError(e instanceof Error ? e.message : '操作失敗');}
    finally {setBusy(false);}
  }
  const currentPage=pages.find(p => p.id===page)!;
  const counts=data?.overview.jobs || {};
  const availableModels=data?.models.filter(m => m.available).length || 0;
  const gpuCount=data?.nodes.reduce((sum,n) => sum+(n.metrics.gpus?.length || 0),0) || 0;
  function navigate(id:string) {setPage(id);setSelected(null);setActionError('');}
  function openJob(id:string) {setSelected(id);setActionError('');}
  const jobProps={jobs:data?.jobs || [],filter,query,setFilter,setQuery,onOpen:openJob,onAll:() => navigate('jobs'),onCreate:() => setCreating(true)};
  return <div className="gcc gcc-shell">
    <a className="gcc-skip" href="#main-content">跳至主要內容</a>
    <aside className="gcc-sidebar">
      <a className="gcc-brand" href="#" onClick={e => {e.preventDefault();navigate('overview');}} aria-label="GPU Control Center 首頁"><span className="gcc-brand-symbol"><Cpu size={25} weight="regular" /></span><span>GPU<span className="gcc-brand-name">Control Center</span></span></a>
      <div className="gcc-workspace"><span className="gcc-workspace-mark"><DesktopTower size={17} /></span><div><strong>本機工作區</strong><span>Local infrastructure</span></div></div>
      <p className="gcc-nav-label">工作空間</p>
      <nav className="gcc-nav" aria-label="主導覽">{pages.map(p => <button key={p.id} aria-current={page===p.id ? 'page' : undefined} className={`gcc-nav-link${page===p.id ? ' is-active' : ''}`} onClick={() => navigate(p.id)}><p.icon size={20} /><span className="gcc-nav-full">{p.title}</span><span className="gcc-nav-short">{p.short}</span>{p.id==='jobs' && !!counts.queued && <b className="gcc-nav-count">{counts.queued}</b>}</button>)}</nav>
      <div className="gcc-sidebar-resource"><span>已連線資源</span><div><strong>{data ? gpuCount : '—'}</strong><span>GPU 裝置</span></div><small>{data ? data.nodes.length : '—'} 台主機 · {availableModels} 個可用模型</small></div>
      <div className="gcc-sidebar-bottom"><GitBranch size={17} /><span>本機部署<span>Ollama · MCP</span></span></div>
    </aside>
    <div className="gcc-work-area">
      <header className="gcc-topbar"><div className="gcc-breadcrumb"><span>工作空間</span><span aria-hidden="true">/</span><strong>{currentPage.title}</strong></div><div className="gcc-topbar-actions"><span className={`gcc-live${error ? ' is-offline' : ''}`}><span aria-hidden="true" />{error ? '連線中斷' : data ? '即時同步' : '連線中'}</span><button className="gcc-icon-button" aria-label="重新整理資料" disabled={manualRefresh} onClick={async () => {setManualRefresh(true);await refresh();setManualRefresh(false);}}><ArrowClockwise size={18} className={manualRefresh ? 'gcc-spin' : ''} /></button><a className="gcc-doc-link" href="/docs" target="_blank" rel="noreferrer"><BookOpen size={17} /><span>API 文件</span></a></div></header>
      <main id="main-content" className="gcc-content" tabIndex={-1}>
        <div className="gcc-page-head"><div><h1>{currentPage.title}</h1><p>{currentPage.description}</p></div><button className="gcc-button gcc-button--primary" onClick={() => setCreating(true)}><Plus size={18} />建立任務</button></div>
        {error && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><div><strong>暫時無法更新資料</strong><p>{error}。{data ? '目前顯示最後一次收到的資料，正在自動重連。' : '正在自動重連。'}</p></div><button className="gcc-button gcc-button--quiet" onClick={() => void refresh()}>重試</button></div>}
        {actionError && !selected && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><p>{actionError}</p><button className="gcc-icon-button" aria-label="關閉錯誤訊息" onClick={() => setActionError('')}><X size={18} /></button></div>}
        {!data && !error && <Skeleton rows={5} />}
        {data && <>
          {page==='overview' && <>
            <section className="gcc-summary" aria-label="平台統計">{[
              {label:'在線主機',value:data.overview.online_nodes,unit:'台',icon:DesktopTower},
              {label:'執行中',value:(counts.running || 0)+(counts.cancelling || 0),unit:'個任務',icon:Pulse},
              {label:'等待排程',value:counts.queued || 0,unit:'個任務',icon:Queue},
              {label:'累計完成',value:counts.completed || 0,unit:'個任務',icon:Check},
            ].map(stat => <div className="gcc-stat" key={stat.label}><span><stat.icon size={17} />{stat.label}</span><div><strong>{stat.value}</strong><span>{stat.unit}</span></div></div>)}</section>
            {!!counts.unknown && <div className="gcc-alert" role="alert"><WarningCircle size={20} /><p>{counts.unknown} 個任務尚未確認停止，已保留原主機工作槽。</p><button className="gcc-button gcc-button--quiet" onClick={() => {navigate('jobs');setFilter('unknown');}}>查看任務</button></div>}
            <div className="gcc-overview-grid"><HardwarePanel nodes={data.nodes} busy={busy} onMaintenance={node => void action(`/nodes/${node.id}/maintenance`,'PATCH',{enabled:!node.maintenance})} /><SlotPanel nodes={data.nodes} jobs={data.jobs} /></div>
            <JobsPanel {...jobProps} compact />
          </>}
          {page==='jobs' && <JobsPanel {...jobProps} />}
          {page==='nodes' && <HardwarePanel nodes={data.nodes} busy={busy} onMaintenance={node => void action(`/nodes/${node.id}/maintenance`,'PATCH',{enabled:!node.maintenance})} />}
          {page==='models' && <section className="gcc-panel"><div className="gcc-panel-head"><div><h2>已安裝模型</h2><p>由主機 Agent 發現，直接使用現有 Ollama 模型。</p></div><span className="gcc-subtle">{availableModels} 個可用</span></div>{data.models.length ? data.models.map(model => <article className="gcc-model-row" key={model.id}><span className="gcc-model-symbol"><Stack size={23} /></span><div className="gcc-model-info"><h3>{model.name}</h3><code>{model.id}</code><p>{model.description}</p></div><div className="gcc-model-state"><Badge value={model.available ? 'online' : 'offline'} /><span>{model.runtime}</span></div><button className="gcc-button" onClick={() => setCreating(true)}>使用模型</button></article>) : <Empty icon={<Stack size={28} />} title="尚未發現模型">確認主機 Agent 已啟動，並在 Ollama 安裝模型。</Empty>}<div className="gcc-inline-note"><Info size={18} /><p>中央目前以每台主機一個工作槽排程，Ollama 負責實際 GPU 選擇。其他程序仍可能占用硬體資源。</p></div></section>}
          <footer className="gcc-footer"><span><span className="gcc-keyline" />中央排程 · 原生 Agent · 真實推論</span><span>{error ? '最後成功同步' : '最後同步'} <time>{updated}</time></span></footer>
        </>}
      </main>
    </div>
    {creating && <CreateJob models={data?.models || []} onClose={() => setCreating(false)} onCreated={async id => {setCreating(false);openJob(id);await refresh();}} />}
    {selected && <DetailPanel onClose={() => setSelected(null)}><div className="gcc-dialog-head"><span>任務詳情</span><button className="gcc-icon-button" aria-label="關閉任務詳情" onClick={() => setSelected(null)}><X size={21} /></button></div>{detail ? <JobDetails detail={detail} explanation={explanation} busy={busy} error={actionError} onAction={path => void action(path)} /> : actionError ? <div className="gcc-dialog-loading gcc-alert" role="alert">{actionError}</div> : <Skeleton rows={4} />}</DetailPanel>}
  </div>;
}

function CopyButton({text,label='複製內容'}:{text:string;label?:string}) {
  const [copied,setCopied]=useState(false);
  const [failed,setFailed]=useState(false);
  useEffect(() => {if(!copied) return;const id=setTimeout(() => setCopied(false),2000);return () => clearTimeout(id);},[copied]);
  return <button className="gcc-button gcc-button--quiet" onClick={async () => {try {await navigator.clipboard.writeText(text);setCopied(true);setFailed(false);} catch {setFailed(true);}}}>{copied ? <Check size={16} /> : <Copy size={16} />}{copied ? '已複製' : failed ? '複製失敗，請手動選取' : label}</button>;
}

function JobDetails({detail,explanation,busy,error,onAction}:{detail:Job;explanation:Explanation|null;busy:boolean;error:string;onAction:(path:string)=>void}) {
  const real=detail.model.startsWith('ollama:');
  const output=detail.runtime_state.text || (typeof detail.result?.text==='string' ? detail.result.text : '');
  return <>
    <div className="gcc-detail-title"><div><h2 id="detail-title">{detail.name}</h2><p><code>{detail.id.slice(0,8)}</code><span>來自 {detail.source}</span></p></div><Badge value={detail.status} /></div>
    {error && <div className="gcc-detail-error gcc-alert" role="alert"><WarningCircle size={20} /><p>{error}</p></div>}
    <div className="gcc-detail-grid"><div className="gcc-detail-main">
      {real && <><section className="gcc-output-section"><div className="gcc-output-head"><h3>生成內容</h3>{output && <CopyButton text={output} />}</div><div className={`gcc-output${output ? '' : ' is-empty'}`} aria-label="生成內容">{output || (detail.status==='queued' ? '等待中央派送後開始生成。' : detail.status==='cancelled' ? '任務已取消。' : detail.status==='failed' ? '本次推論未產生內容。' : '模型準備中，文字會顯示在這裡。')}</div><div className="gcc-output-meta"><Progress job={detail} />{typeof detail.result?.tokens_per_second==='number' && <span>{detail.result.tokens_per_second.toFixed(1)} tokens/s</span>}</div></section><section className="gcc-prompt-section"><h3>提示詞</h3><p>{detail.parameters.prompt}</p></section></>}
      {!real && <div className="gcc-inline-note"><Info size={18} /><p>這是模擬工作，沒有執行 AI 推論。</p></div>}
      <details className="gcc-disclosure" open={['queued','unknown','cancelling','failed'].includes(detail.status)}><summary><ListBullets size={18} />排程說明</summary><div>{explanation ? <><p>{explanation.summary}</p>{explanation.queue_rank !== null && <p className="gcc-muted">目前排序 {explanation.queue_rank} / {explanation.queue_size}（即時快照）</p>}{detail.status==='queued' && explanation.candidates.map(n => <div className="gcc-candidate" key={n.node_id}><strong>{n.name}</strong><p>{n.ready ? '可接單，等待 Agent 輪詢' : n.blockers.map(b => blockerLabels[b] || b).join('、')}</p>{n.holders.map(h => <p className="gcc-muted" key={h.id}>目前持有者：{h.source} · {h.name}</p>)}</div>)}</> : <p className="gcc-muted">正在讀取排程狀態…</p>}</div></details>
      <details className="gcc-disclosure"><summary><Pulse size={18} />執行紀錄 <span>{detail.events?.length || 0}</span></summary><ol className="gcc-timeline">{detail.events?.map(e => <li key={e.id}><time dateTime={e.created_at}>{time(e.created_at)}</time><p>{e.message}</p></li>)}</ol></details>
      {detail.result && <details className="gcc-disclosure gcc-raw"><summary><GitBranch size={18} />原始回應</summary><pre>{JSON.stringify(detail.result,null,2)}</pre></details>}
    </div><aside className="gcc-detail-aside"><h3>執行資訊</h3><dl><dt>模型</dt><dd>{detail.model.replace('ollama:','')}</dd><dt>來源應用</dt><dd>{detail.source}</dd><dt>執行主機</dt><dd>{detail.node_id || '等待分配'}</dd><dt>優先級</dt><dd>{detail.priority}</dd><dt>建立時間</dt><dd>{time(detail.created_at)}</dd>{detail.finished_at && <><dt>完成時間</dt><dd>{time(detail.finished_at)}</dd></>}</dl>
      {detail.error && <div className="gcc-alert"><WarningCircle size={18} /><p>{detail.error}</p></div>}
      {['queued','running','unknown'].includes(detail.status) && <button className="gcc-button gcc-button--danger" disabled={busy} onClick={() => onAction(`/jobs/${detail.id}/cancel`)}>{busy ? <CircleNotch size={17} className="gcc-spin" /> : <X size={17} />}取消任務</button>}
      {['failed','cancelled'].includes(detail.status) && <button className="gcc-button" disabled={busy} onClick={() => onAction(`/jobs/${detail.id}/retry`)}><ArrowClockwise size={17} />建立重試任務</button>}
      {detail.status==='cancelling' && <p className="gcc-cancel-note" role="status">已請求取消，等待本次生成結束並確認停止。</p>}
      {real && <p className="gcc-detail-note"><Info size={16} />生成長度不固定。完成後會回報實際 token 數。</p>}
    </aside></div>
  </>;
}

function DetailPanel({onClose,children}:{onClose:()=>void;children:React.ReactNode}) {
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(() => {const dialog=ref.current;dialog?.showModal();return () => dialog?.close();},[]);
  return <dialog ref={ref} className="gcc-dialog gcc-detail-dialog" aria-labelledby="detail-title" onCancel={onClose} onClick={e => {if(e.target===e.currentTarget) {const box=e.currentTarget.getBoundingClientRect();if(e.clientX<box.left || e.clientX>box.right || e.clientY<box.top || e.clientY>box.bottom) onClose();}}}>{children}</dialog>;
}

function CreateJob({models,onClose,onCreated}:{models:Model[];onClose:()=>void;onCreated:(id:string)=>Promise<void>}) {
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [model,setModel]=useState(models.find(m => m.available)?.id || models[0]?.id || '');
  const [name,setName]=useState('文字推論');
  const [source,setSource]=useState('console');
  const [prompt,setPrompt]=useState('');
  const [maxTokens,setMaxTokens]=useState('256');
  const [priority,setPriority]=useState('0');
  const [steps,setSteps]=useState('20');
  const [promptTouched,setPromptTouched]=useState(false);
  const ref=useRef<HTMLDialogElement>(null);
  useEffect(() => {const dialog=ref.current;dialog?.showModal();return () => dialog?.close();},[]);
  const demo=model==='demo.progress';
  const promptError=!demo && promptTouched && !prompt.trim() ? '請輸入提示詞，模型才能開始生成。' : '';
  async function submit(e:React.FormEvent<HTMLFormElement>) {
    e.preventDefault();setPromptTouched(true);
    if(!demo && !prompt.trim()) {ref.current?.querySelector('textarea')?.focus();return;}
    setBusy(true);setError('');
    try {const job=await api<Job>('/jobs','POST',{name:name.trim(),source:source.trim(),model,prompt:demo ? '' : prompt,max_tokens:demo ? 256 : Number(maxTokens),steps:demo ? Number(steps) : 20,priority:Number(priority)});await onCreated(job.id);}
    catch(e) {setError(e instanceof Error ? e.message : '建立失敗，請稍後重試');setBusy(false);}
  }
  return <dialog ref={ref} className="gcc-dialog gcc-create-dialog" aria-labelledby="create-title" onCancel={e => {if(busy) e.preventDefault();else onClose();}}><form onSubmit={e => void submit(e)}>
    <div className="gcc-dialog-head"><span><Plus size={18} />新增工作</span><button type="button" className="gcc-icon-button" aria-label="關閉建立任務" onClick={onClose} disabled={busy}><X size={21} /></button></div>
    <div className="gcc-create-body"><h2 id="create-title">建立推論任務</h2><p className="gcc-create-description">選擇模型並輸入內容，由中央安排執行。</p>
      <label className="gcc-field"><span>模型 <small>必填</small></span><select value={model} onChange={e => setModel(e.target.value)} required disabled={busy}><option value="" disabled>選擇已安裝模型</option>{models.map(m => <option key={m.id} value={m.id}>{m.name}{m.available ? '' : '（離線，可排隊）'}</option>)}</select><span className="gcc-field-help">{models.length ? '使用主機上已安裝的 Ollama 模型。' : '尚無模型。請確認 Agent 已連線，並在 Ollama 安裝模型。'}</span></label>
      {!demo && <label className="gcc-field"><span>提示詞 <small>必填</small></span><textarea name="prompt" rows={6} value={prompt} onChange={e => setPrompt(e.target.value)} onBlur={() => setPromptTouched(true)} autoFocus required aria-invalid={!!promptError} aria-describedby="prompt-help" maxLength={32000} placeholder="例如：請用三點說明 GPU 排程的用途。" /><span id="prompt-help" className={`gcc-field-help${promptError ? ' is-error' : ''}`}>{promptError || '文字會逐步顯示在任務詳情中。'}</span></label>}
      <label className="gcc-field gcc-token-field"><span>{demo ? '模擬步數' : '最大生成 tokens'}</span><input type="number" min={demo ? 5 : 1} max={demo ? 120 : 2048} value={demo ? steps : maxTokens} onChange={e => demo ? setSteps(e.target.value) : setMaxTokens(e.target.value)} required disabled={busy} /><span className="gcc-field-help">{demo ? '每一步約 0.5 秒，不執行 AI 推論。' : '生成上限 1–2048，實際長度由模型決定。'}</span></label>
      <details className="gcc-disclosure gcc-advanced"><summary>進階設定</summary><div className="gcc-form-grid"><label className="gcc-field"><span>任務名稱</span><input value={name} onChange={e => setName(e.target.value)} required maxLength={120} disabled={busy} /></label><label className="gcc-field"><span>來源應用</span><input value={source} onChange={e => setSource(e.target.value)} required maxLength={80} disabled={busy} /></label><label className="gcc-field"><span>優先級（0–10）</span><input type="number" min={0} max={10} value={priority} onChange={e => setPriority(e.target.value)} required disabled={busy} /></label></div></details>
      {error && <div className="gcc-alert" role="alert"><WarningCircle size={19} /><p>{error}</p></div>}
    </div><div className="gcc-create-actions"><span><Lightning size={16} />{demo ? '模擬執行' : '真實推論'}</span><div><button type="button" className="gcc-button" onClick={onClose} disabled={busy}>取消</button><button className="gcc-button gcc-button--primary" disabled={busy || !model}>{busy ? <CircleNotch size={17} className="gcc-spin" /> : <Plus size={17} />}{busy ? '提交中' : '開始推論'}</button></div></div>
  </form></dialog>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><IconContext.Provider value={{weight:"regular", "aria-hidden":true}}><App/></IconContext.Provider></React.StrictMode>);
