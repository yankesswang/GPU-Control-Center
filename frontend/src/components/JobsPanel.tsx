import { ArrowClockwise, CheckCircle, Clock, MagnifyingGlass, Stack, X } from '@phosphor-icons/react';
import type { Job } from '../api';
import { useI18n } from '../i18n';
import { Badge, Empty, UsageBar } from './ui';

export function Progress({ job }: { job: Job }) {
  const { t } = useI18n();
  if (job.parameters.model_action) {
    return <span className="gcc-progress-value">{
      job.status === 'completed' ? t('操作已完成', 'Completed')
        : job.status === 'running' ? (job.parameters.model_action === 'load' ? t('載入中', 'Loading') : t('卸載中', 'Unloading'))
        : job.status === 'queued' ? t('等待派送', 'Queued')
        : job.status === 'unknown' ? t('狀態待確認', 'Unconfirmed')
        : job.status === 'failed' ? t('操作失敗', 'Failed')
        : t('已取消', 'Cancelled')
    }</span>;
  }
  if (job.model.startsWith('ollama:') || job.model.startsWith('lmstudio:')) {
    if (job.status === 'completed') return <span className="gcc-progress-value"><CheckCircle size={15} />{job.runtime_state.tokens_generated==null ? t('已完成', 'Completed') : <>{job.runtime_state.tokens_generated}<small>tokens</small></>}</span>;
    if (job.status === 'running') return <span className="gcc-progress-value">{job.runtime_state.phase === 'loading' ? t('載入模型', 'Loading model') : t(`生成中 · ${job.runtime_state.chunks_received ?? 0} 片段`, `Generating · ${job.runtime_state.chunks_received ?? 0} chunks`)}</span>;
    return <span className="gcc-muted">{job.status === 'queued' ? t('等待派送', 'Pending dispatch') : job.status === 'cancelled' ? t('已停止', 'Stopped') : job.status === 'cancelling' ? t('等待確認停止', 'Stopping…') : '—'}</span>;
  }
  return <div className="gcc-demo-progress"><UsageBar value={job.progress} label={t(`${job.name} 模擬進度`, `${job.name} simulated progress`)} /><span>{Math.round(job.progress)}%</span></div>;
}

export function JobsPanel({ jobs, filter, query, setFilter, setQuery, onOpen, compact = false, onAll, onCreate }: {
  jobs: Job[]; filter: string; query: string; setFilter: (value: string) => void; setQuery: (value: string) => void;
  onOpen: (id: string) => void; compact?: boolean; onAll: () => void; onCreate: () => void;
}) {
  const { t, statusLabel, formatTime, formatClock } = useI18n();
  const filtered = jobs.filter(j => (filter === 'all' || j.status === filter) && `${j.name} ${j.source} ${j.model} ${j.id}`.toLowerCase().includes(query.toLowerCase()));
  const visible = filtered.slice(0, compact ? 5 : 100);
  return <section className="gcc-panel gcc-jobs" aria-labelledby="jobs-title"><div className="gcc-panel-head"><div><h2 id="jobs-title">{compact ? t('最近任務', 'Recent Jobs') : t('任務佇列', 'Job Queue')}</h2><p>{compact ? t('查看各應用的執行狀態與生成結果', 'Monitor execution status and generation outputs across apps') : t('最近 100 筆 · 依建立時間排序', 'Latest 100 jobs · Sorted by creation time')}</p></div>{compact ? <button className="gcc-button gcc-button--quiet" onClick={onAll}>{t('查看全部', 'View All')}</button> : <span className="gcc-subtle">{t(`${filtered.length} 筆符合條件`, `${filtered.length} matching`)}</span>}</div>
    <div className="gcc-table-tools"><label className="gcc-search"><span className="sr-only">{t('搜尋任務', 'Search jobs')}</span><MagnifyingGlass size={18} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder={t('搜尋名稱、來源或模型', 'Search by name, source, or model')} />{query && <button aria-label={t('清除搜尋', 'Clear search')} className="gcc-icon-button" onClick={() => setQuery('')}><X size={16} /></button>}</label><label className="gcc-filter"><span className="sr-only">{t('篩選任務狀態', 'Filter job status')}</span><select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">{t('所有狀態', 'All Statuses')}</option>{['queued', 'running', 'completed', 'cancelled', 'failed', 'unknown', 'cancelling'].map(s => <option key={s} value={s}>{statusLabel(s)}</option>)}</select></label></div>
    {visible.length ? <>
      <div className="gcc-desktop-table"><table><thead><tr><th>{t('任務', 'Job')}</th><th>{t('來源 / 模型', 'Source / Model')}</th><th>{t('狀態', 'Status')}</th><th>{t('生成狀態', 'Progress')}</th><th>{t('建立時間', 'Created')}</th></tr></thead><tbody>{visible.map(job => <tr key={job.id}><td><button className="gcc-job-link" onClick={() => onOpen(job.id)}>{job.name}</button><span className="gcc-job-id">{job.id.slice(0, 8)}</span></td><td><span className="gcc-source">{job.source}</span><span className="gcc-model-name">{job.model.replace(/^(ollama|lmstudio):/, '')}</span></td><td><Badge value={job.status} /></td><td><Progress job={job} /></td><td><time className="gcc-time" dateTime={job.created_at} title={formatTime(job.created_at)}>{formatClock(job.created_at)}</time></td></tr>)}</tbody></table></div>
      <div className="gcc-mobile-jobs">{visible.map(job => <article className="gcc-mobile-job" key={job.id}><div><button className="gcc-job-link" onClick={() => onOpen(job.id)}>{job.name}</button><Badge value={job.status} /></div><p>{job.source}<span>{job.model.replace(/^(ollama|lmstudio):/, '')}</span></p><div><Progress job={job} /><time dateTime={job.created_at}>{formatClock(job.created_at)}</time></div></article>)}</div>
    </> : <Empty icon={jobs.length ? <MagnifyingGlass size={28} /> : <Stack size={28} />} title={jobs.length ? t('沒有符合條件的任務', 'No matching jobs') : t('準備好開始推論', 'Ready for Inference')} action={jobs.length ? <button className="gcc-button" onClick={() => { setQuery(''); setFilter('all'); }}><ArrowClockwise size={16} />{t('清除篩選', 'Clear Filters')}</button> : <button className="gcc-button" onClick={onCreate}>{t('建立第一個任務', 'Create First Job')}</button>}>{jobs.length ? t('試著調整搜尋文字，或切換狀態篩選。', 'Try adjusting your search query or status filter.') : t('選擇已安裝的模型，輸入提示詞並交給中央排程。', 'Select an installed model, enter a prompt, and dispatch to the central scheduler.')}</Empty>}
    {!!visible.length && <div className="gcc-table-footer"><span>{t(`顯示 ${visible.length} / ${filtered.length} 筆`, `Showing ${visible.length} of ${filtered.length}`)}</span><span><Clock size={14} />{t('狀態持續同步', 'Live sync active')}</span></div>}
  </section>;
}
