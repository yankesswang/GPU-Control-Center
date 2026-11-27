import { ArrowClockwise, CheckCircle, Clock, MagnifyingGlass, Stack, X } from '@phosphor-icons/react';
import type { Job } from '../api';
import { Badge, clock, Empty, statusLabels, time, UsageBar } from './ui';

export function Progress({ job }: { job: Job }) {
  if (job.model.startsWith('ollama:')) {
    if (job.status === 'completed') return <span className="gcc-progress-value"><CheckCircle size={15} />{job.runtime_state.tokens_generated ?? 0}<small>tokens</small></span>;
    if (job.status === 'running') return <span className="gcc-progress-value">{job.runtime_state.phase === 'loading' ? '載入模型' : `生成中 · ${job.runtime_state.chunks_received ?? 0} 片段`}</span>;
    return <span className="gcc-muted">{job.status === 'queued' ? '等待派送' : job.status === 'cancelled' ? '已停止' : job.status === 'cancelling' ? '等待確認停止' : '—'}</span>;
  }
  return <div className="gcc-demo-progress"><UsageBar value={job.progress} label={`${job.name} 模擬進度`} /><span>{Math.round(job.progress)}%</span></div>;
}

export function JobsPanel({ jobs, filter, query, setFilter, setQuery, onOpen, compact = false, onAll, onCreate }: {
  jobs: Job[]; filter: string; query: string; setFilter: (value: string) => void; setQuery: (value: string) => void;
  onOpen: (id: string) => void; compact?: boolean; onAll: () => void; onCreate: () => void;
}) {
  const filtered = jobs.filter(j => (filter === 'all' || j.status === filter) && `${j.name} ${j.source} ${j.model} ${j.id}`.toLowerCase().includes(query.toLowerCase()));
  const visible = filtered.slice(0, compact ? 5 : 100);
  return <section className="gcc-panel gcc-jobs" aria-labelledby="jobs-title"><div className="gcc-panel-head"><div><h2 id="jobs-title">{compact ? '最近任務' : '任務佇列'}</h2><p>{compact ? '查看各應用的執行狀態與生成結果' : '最近 100 筆 · 依建立時間排序'}</p></div>{compact ? <button className="gcc-button gcc-button--quiet" onClick={onAll}>查看全部</button> : <span className="gcc-subtle">{filtered.length} 筆符合條件</span>}</div>
    <div className="gcc-table-tools"><label className="gcc-search"><span className="sr-only">搜尋任務</span><MagnifyingGlass size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜尋名稱、來源或模型" />{query && <button aria-label="清除搜尋" className="gcc-icon-button" onClick={() => setQuery('')}><X size={16} /></button>}</label><label className="gcc-filter"><span className="sr-only">篩選任務狀態</span><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">所有狀態</option>{['queued', 'running', 'completed', 'cancelled', 'failed', 'unknown', 'cancelling'].map(s => <option key={s} value={s}>{statusLabels[s]}</option>)}</select></label></div>
    {visible.length ? <>
      <div className="gcc-desktop-table"><table><thead><tr><th>任務</th><th>來源 / 模型</th><th>狀態</th><th>生成狀態</th><th>建立時間</th></tr></thead><tbody>{visible.map(job => <tr key={job.id}><td><button className="gcc-job-link" onClick={() => onOpen(job.id)}>{job.name}</button><span className="gcc-job-id">{job.id.slice(0, 8)}</span></td><td><span className="gcc-source">{job.source}</span><span className="gcc-model-name">{job.model.replace('ollama:', '')}</span></td><td><Badge value={job.status} /></td><td><Progress job={job} /></td><td><time className="gcc-time" dateTime={job.created_at} title={time(job.created_at)}>{clock(job.created_at)}</time></td></tr>)}</tbody></table></div>
      <div className="gcc-mobile-jobs">{visible.map(job => <article className="gcc-mobile-job" key={job.id}><div><button className="gcc-job-link" onClick={() => onOpen(job.id)}>{job.name}</button><Badge value={job.status} /></div><p>{job.source}<span>{job.model.replace('ollama:', '')}</span></p><div><Progress job={job} /><time dateTime={job.created_at}>{clock(job.created_at)}</time></div></article>)}</div>
    </> : <Empty icon={jobs.length ? <MagnifyingGlass size={28} /> : <Stack size={28} />} title={jobs.length ? '沒有符合條件的任務' : '準備好開始推論'} action={jobs.length ? <button className="gcc-button" onClick={() => { setQuery(''); setFilter('all'); }}><ArrowClockwise size={16} />清除篩選</button> : <button className="gcc-button" onClick={onCreate}>建立第一個任務</button>}>{jobs.length ? '試著調整搜尋文字，或切換狀態篩選。' : '選擇已安裝的模型，輸入提示詞並交給中央排程。'}</Empty>}
    {!!visible.length && <div className="gcc-table-footer"><span>顯示 {visible.length} / {filtered.length} 筆</span><span><Clock size={14} />狀態持續同步</span></div>}
  </section>;
}
