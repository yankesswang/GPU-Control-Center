import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {api, type Job, type Model, type Node, type Overview, type Explanation} from './api';
import './style.css';

const labels: Record<string, string> = {queued:'排隊中', running:'執行中', completed:'已完成', failed:'失敗', cancelled:'已取消', cancelling:'取消中', unknown:'待確認', online:'在線', offline:'離線', maintenance:'維護中'};
const blockerLabels: Record<string,string> = {unsupported_capability:'不支援此能力',offline:'主機離線',maintenance:'維護中',unconfirmed_execution:'有執行狀態待確認的工作',slot_occupied:'工作槽已占用'};
const time = (value: string) => new Date(value).toLocaleString('zh-TW', {hour12:false});
function Status({value}: {value: string}) { return <span className={`status ${value}`}><span aria-hidden="true"/>{labels[value] || value}</span>; }
function Meter({value, label}: {value: number; label: string}) {return <div className="meter" role="progressbar" aria-label={label} aria-valuenow={Math.round(value)} aria-valuemin={0} aria-valuemax={100}><i style={{width:`${Math.max(0,Math.min(100,value))}%`}}/></div>;}

function App() {
  const [page, setPage] = useState('overview');
  const [data, setData] = useState<{nodes: Node[]; jobs: Job[]; models: Model[]; overview: Overview} | null>(null);
  const [error, setError] = useState('');
  const [actionError, setActionError] = useState('');
  const [updated, setUpdated] = useState('');
  const [creating, setCreating] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Job | null>(null);
  const [explanation, setExplanation] = useState<Explanation | null>(null);
  const [filter, setFilter] = useState('all');
  const [query, setQuery] = useState('');
  const [busy, setBusy] = useState(false);
  const refreshing = useRef(false);
  const refresh = useCallback(async () => {
    if (refreshing.current) return;
    refreshing.current = true;
    try {
      const [nodes, jobs, models, overview] = await Promise.all([api<Node[]>('/nodes'), api<Job[]>('/jobs'), api<Model[]>('/models'), api<Overview>('/overview')]);
      setData({nodes, jobs, models, overview}); setError(''); setUpdated(new Date().toLocaleTimeString('zh-TW', {hour12:false}));
    } catch(e) {setError(e instanceof Error ? e.message : '無法連線到中央服務');}
    finally {refreshing.current = false;}
  }, []);
  useEffect(() => {void refresh(); const id = setInterval(() => void refresh(), 2000); return () => clearInterval(id);}, [refresh]);
  useEffect(() => {
    if (!selected) {setDetail(null); setExplanation(null); return;}
    let current = true;
    setDetail(null); setExplanation(null);
    const load = async () => {try {const [result, why] = await Promise.all([api<Job>(`/jobs/${selected}`), api<Explanation>(`/jobs/${selected}/explain`)]); if(current) {setDetail(result); setExplanation(why);}} catch(e) {if(current) setActionError(e instanceof Error ? e.message : '讀取失敗');}};
    void load(); const id = setInterval(() => void load(), 2000);
    return () => {current = false; clearInterval(id);};
  }, [selected]);
  async function action(path: string, method = 'POST', body?: unknown) {
    setBusy(true); setActionError('');
    try {const result = await api<Job>(path, method, body); await refresh(); if(path.endsWith('/retry')) setSelected(result.id); else if(selected && path.startsWith(`/jobs/${selected}/`)) setDetail(await api<Job>(`/jobs/${selected}`));}
    catch(e) {setActionError(e instanceof Error ? e.message : '操作失敗');} finally {setBusy(false);}
  }
  const titles: Record<string, string> = {overview:'運算總覽', jobs:'任務佇列', nodes:'運算主機', models:'模型與能力'};
  const counts = data?.overview.jobs || {};
  const filtered = data?.jobs.filter(j => (filter === 'all' || j.status === filter) && `${j.name} ${j.source} ${j.id}`.toLowerCase().includes(query.toLowerCase())) || [];
  return <div className="shell">
    <aside className="sidebar"><a className="brand" href="#" onClick={() => setPage('overview')}><span className="brand-mark">GPU</span><span>Control Center<span className="brand-sub">GPU RESOURCE PLATFORM</span></span></a>
      <p className="nav-label">工作空間</p><nav aria-label="主導覽">{Object.entries(titles).map(([key,title]) => <button key={key} className={page === key ? 'nav active' : 'nav'} onClick={() => {setPage(key); setSelected(null);}}><span className="nav-symbol" aria-hidden="true">{({overview:'◫',jobs:'≡',nodes:'▣',models:'◇'} as Record<string,string>)[key]}</span>{title}{key === 'jobs' && !!counts.queued && <b>{counts.queued}</b>}</button>)}</nav>
      <div className="sidebar-bottom"><span className={`connection ${error ? 'disconnected' : ''}`}/><span>{error ? '中央服務連線中斷' : data ? '中央服務已連線' : '正在連線'}<small>本機開發環境 · v0.1</small></span></div>
    </aside>
    <main>
      <header className="topbar"><span>工作空間 <span className="slash">/</span> <strong>{titles[page]}</strong></span><a href="/docs" target="_blank" rel="noreferrer">API 文件</a></header>
      <div className="content"><div className="page-heading"><div><div className="eyebrow">GPU CONTROL CENTER</div><h1>{titles[page]}</h1><p>{page === 'overview' ? '集中掌握資源，讓每一個推論任務有序執行。' : page === 'jobs' ? '追蹤任務生命週期、進度與執行紀錄。' : page === 'nodes' ? '查看主機心跳、硬體狀態與接單能力。' : '讓應用透過一致的能力名稱提交工作。'}</p></div><button className="primary" onClick={() => setCreating(true)}>＋ 建立任務</button></div>
      {error && <div className="alert" role="alert">中央服務暫時無法更新：{error}。正在自動重連。<button onClick={() => void refresh()}>重試連線</button></div>}
      {actionError && <div className="alert" role="alert">{actionError}<button onClick={() => setActionError('')}>關閉</button></div>}
      {!data && !error && <div className="empty">正在讀取主機與任務狀態…</div>}
      {data && <>
        {page === 'overview' && <><section className="stats" aria-label="平台統計">{[['在線主機',data.overview.online_nodes,'台可接單'],['執行任務',(counts.running || 0) + (counts.cancelling || 0),'個進行中'],['等待排程',counts.queued || 0,'個任務'],['累計完成',counts.completed || 0,'個已完成']].map(([label,value,hint]) => <div className="stat" key={label}><span>{label}</span><strong>{value}</strong><small>{hint}</small></div>)}</section>
          <div className="notice"><span className="notice-label">驗證模式</span><span>預設 Agent 的 GPU 數據與推論進度為模擬資料。可先建立任務，驗證排程到結果保存的完整流程。</span></div>
          {(counts.unknown || 0) > 0 && <div className="alert">有 {counts.unknown} 個任務狀態待確認，已保留原主機執行槽並停止自動重派。</div>}
        </>}
        {(page === 'overview' || page === 'nodes') && <section className="panel"><div className="section-heading"><div><h2>運算資源</h2><p>{data.nodes.length} 台已登錄主機</p></div><span className="live">每 2 秒更新</span></div><div className="nodes-grid">{data.nodes.length ? data.nodes.map(node => <article className="node" key={node.id}><div className="node-head"><div><h3>{node.name}</h3><span className="mono">{node.mode === 'demo' ? 'SIMULATED WORKER' : 'HOST TELEMETRY'}</span></div><Status value={node.status}/></div><div className="hardware">{node.metrics.gpus?.length ? node.metrics.gpus.map((gpu,i) => <div key={i}><div className="hardware-title">{gpu.name}</div><div className="metric-label"><span>GPU 使用率</span><strong>{gpu.utilization}%</strong></div><Meter value={gpu.utilization} label="GPU 使用率"/><div className="metric-label"><span>顯存</span><strong>{(gpu.memory_used_mb/1024).toFixed(1)} / {(gpu.memory_total_mb/1024).toFixed(1)} GB</strong></div><Meter value={gpu.memory_used_mb/gpu.memory_total_mb*100} label="顯存使用率"/><div className="hardware-footer"><span>溫度 {gpu.temperature_c ?? '—'} °C</span><span>功耗 {gpu.power_w ?? '—'} W</span></div></div>) : <p className="muted">尚未偵測到 GPU</p>}</div><div className="cpu-row"><span>CPU <b>{node.metrics.cpu_percent ?? '—'}%</b></span><span>RAM <b>{node.metrics.ram_percent ?? '—'}%</b></span></div><div className="node-footer"><span>{node.mode === 'demo' ? '模擬資料' : '實際主機數據'}<small>心跳 {time(node.last_seen)}</small></span><button disabled={busy} onClick={() => void action(`/nodes/${node.id}/maintenance`, 'PATCH', {enabled:!node.maintenance})}>{node.maintenance ? '恢復接單' : '停止接單'}</button></div></article>) : <div className="empty">尚無主機連線。啟動 demo-agent 或安裝主機 Agent 後，會自動出現在這裡。</div>}</div></section>}
        {(page === 'overview' || page === 'jobs') && <section className="panel"><div className="section-heading"><div><h2>{page === 'overview' ? '最近任務' : '所有任務'}</h2><p>顯示最近 100 筆任務 · 依建立時間排序</p></div>{page === 'overview' && <button onClick={() => setPage('jobs')}>查看全部</button>}</div><div className="filters"><label className="search"><span>搜尋</span><input value={query} onChange={e => setQuery(e.target.value)} placeholder="任務名稱、來源或 ID"/></label><label><span className="sr-only">篩選狀態</span><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">所有狀態</option>{['queued','running','completed','cancelled','failed','unknown','cancelling'].map(s => <option value={s} key={s}>{labels[s]}</option>)}</select></label></div><div className="table-scroll"><table><thead><tr><th>任務名稱</th><th>來源 / 模型</th><th>狀態</th><th>進度</th><th>執行主機</th><th>建立時間</th></tr></thead><tbody>{filtered.slice(0,page === 'overview' ? 6 : 100).map(job => <tr key={job.id}><td><button className="job-link" onClick={() => setSelected(job.id)}>{job.name}</button><small className="mono">{job.id.slice(0,8)}</small></td><td>{job.source}<small>{job.model}</small></td><td><Status value={job.status}/></td><td><div className="progress-cell"><Meter value={job.progress} label={`${job.name} 進度`}/><span>{Math.round(job.progress)}%</span></div></td><td className="mono">{job.node_id || '等待分配'}</td><td className="date">{time(job.created_at)}</td></tr>)}</tbody></table>{!filtered.length && <div className="empty"><h3>{data.jobs.length ? '沒有符合條件的任務' : '任務佇列已就緒'}</h3><p>{data.jobs.length ? '請調整搜尋文字或狀態篩選。' : '建立第一個模擬任務，觀察中央派送與即時進度。'}</p>{!data.jobs.length && <button onClick={() => setCreating(true)}>建立第一個任務</button>}</div>}</div></section>}
        {page === 'models' && <section className="panel"><div className="section-heading"><div><h2>已註冊能力</h2><p>第一版提供模擬執行器；AI 推論引擎尚待接入。</p></div></div>{data.models.map(model => <article className="model-row" key={model.id}><div><h3>{model.name}</h3><p>{model.description}</p><code>{model.id}</code></div><span className="tag">{model.runtime}</span></article>)}<div className="integration"><h3>接入真實主機</h3><p>主機 Agent 可回報實際 CPU、RAM 與 NVIDIA GPU 狀態。目前 host 模式僅監控，不接收推論任務。安裝與設定方式請參考 repo 的 README。</p></div></section>}
        <footer className="page-footer"><span>PostgreSQL 持久化 · 中央派送 · Agent 回報</span><span>最後同步 {updated}</span></footer>
      </>}
      </div>
    </main>
    {creating && <CreateJob onClose={() => setCreating(false)} onCreated={async id => {setCreating(false); setSelected(id); await refresh();}}/>}
    {selected && <DetailPanel onClose={() => setSelected(null)}><div className="drawer-heading"><h2 id="detail-title">任務詳情</h2><button autoFocus aria-label="關閉任務詳情" onClick={() => setSelected(null)}>關閉</button></div>{detail ? <><h3 className="detail-name">{detail.name}</h3><Status value={detail.status}/><p className="mono detail-id">{detail.id}</p><Meter value={detail.progress} label="任務完成進度"/><p>{Math.round(detail.progress)}% · {detail.steps} 個模擬步驟</p><dl><dt>來源</dt><dd>{detail.source}</dd><dt>模型能力</dt><dd>{detail.model}</dd><dt>執行主機</dt><dd>{detail.node_id || '等待分配'}</dd><dt>優先級</dt><dd>{detail.priority}</dd><dt>建立時間</dt><dd>{time(detail.created_at)}</dd></dl>{detail.error && <div className="alert">{detail.error}</div>}<div className="detail-actions">{['queued','running','unknown'].includes(detail.status) && <button disabled={busy} onClick={() => void action(`/jobs/${detail.id}/cancel`)}>取消任務</button>}{['failed','cancelled'].includes(detail.status) && <button disabled={busy} onClick={() => void action(`/jobs/${detail.id}/retry`)}>建立重試任務</button>}</div><h3>排程說明</h3>{explanation && <div className="schedule-explanation"><p>{explanation.summary}</p>{explanation.queue_rank !== null && <p>目前排序 {explanation.queue_rank} / {explanation.queue_size}（快照）</p>}{detail.status === "queued" && explanation.candidates.map(n => <div key={n.node_id}><strong>{n.name}</strong><p>{n.ready ? "可接單，等待輪詢" : n.blockers.map(b => (blockerLabels[b] || b)).join("、")}</p>{n.holders.map(h => <small key={h.id}>持有者：{h.source} · {h.name}</small>)}</div>)}</div>}<h3>執行紀錄</h3><ol className="timeline">{detail.events?.map(e => <li key={e.id}><small>{time(e.created_at)}</small><p>{e.message}</p></li>)}</ol>{detail.result && <><h3>執行結果</h3><pre>{JSON.stringify(detail.result,null,2)}</pre></>}</> : <p>讀取任務中…</p>}</DetailPanel>}
    </div>;
}

function DetailPanel({onClose, children}: {onClose:()=>void; children: React.ReactNode}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {ref.current?.showModal();}, []);
  return <dialog ref={ref} className="drawer" aria-labelledby="detail-title" onCancel={onClose}>{children}</dialog>;
}

function CreateJob({onClose,onCreated}: {onClose:()=>void; onCreated:(id:string)=>Promise<void>}) {
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {ref.current?.showModal();},[]);
  async function submit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault(); setBusy(true); setError('');
    const form = new FormData(e.currentTarget);
    try {const job = await api<Job>('/jobs','POST',{name:form.get('name'), source:form.get('source'), steps:Number(form.get('steps')), priority:Number(form.get('priority')), model:'demo.progress'}); await onCreated(job.id);}
    catch(e) {setError(e instanceof Error ? e.message : '建立失敗'); setBusy(false);}
  }
  return <dialog ref={ref} className="create-dialog" onCancel={onClose}><form onSubmit={e => void submit(e)}><div className="drawer-heading"><h2>建立推論任務</h2><button type="button" onClick={onClose} aria-label="關閉建立任務">關閉</button></div><p className="muted">提交至中央佇列，由符合能力的 Agent 接單。</p><label>任務名稱<input name="name" required maxLength={120} defaultValue="第一個排程驗證" autoFocus/></label><label>來源應用<input name="source" required maxLength={80} defaultValue="console"/></label><label>模型能力<select name="model"><option>demo.progress · 模擬進度驗證</option></select></label><div className="form-grid"><label>執行步數<input name="steps" type="number" min={5} max={120} defaultValue={20} required/></label><label>優先級（0–10）<input name="priority" type="number" min={0} max={10} defaultValue={0} required/></label></div><p className="form-hint">每步約 0.5 秒。此工作用於驗證排程，不產生 AI 推論結果。</p>{error && <div className="alert" role="alert">{error}</div>}<div className="form-actions"><button type="button" onClick={onClose}>取消</button><button className="primary" disabled={busy}>{busy ? '提交中…' : '提交任務'}</button></div></form></dialog>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
