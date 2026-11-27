import {useMemo, useState} from 'react';
import {
  CheckSquare, CircleNotch, DotsSixVertical, DownloadSimple, Eject, Eye, EyeSlash, Info, LockSimple, MagnifyingGlass, Stack, WarningCircle, X,
} from '@phosphor-icons/react';
import { modelDisplayName, type ConfiguredPlacement, type Job, type LoadedModel, type Node } from '../api';
import { useI18n } from '../i18n';
import { EditableModelName } from './ui';
import { buildLmStudioDragPayload, useModelDrag } from './modelDrag';

export type RuntimeHandler = (node:Node, runtime:'lmstudio'|'ollama')=>void;
export type ModelActionHandler = (node:Node, model:string, action:'load'|'unload', gpuIndices?:number[]|null, replace?:boolean)=>void;
export type ModelRenameHandler = (node:Node, model:string, alias:string)=>void;
export type ModelHiddenHandler = (node:Node, hide:string[], unhide:string[])=>void;

const size = (bytes?:number|null) => bytes==null ? '—' : `${(bytes/1024**3).toFixed(2)} GiB`;
const formatMb = (mb?:number|null) => mb==null ? '—' : mb>=1024 ? `${(mb/1024).toFixed(2)} GiB` : `${Math.round(mb)} MiB`;
const gpuLabel = (idx:number) => `GPU ${String(idx).padStart(2,'0')}`;
const shortGpuLabel = (idx:number) => `GPU ${idx}`;
const shortGpuName = (name:string) => name.replace(/^NVIDIA\s+(GeForce\s+)?/i, '');

function configuredTargetKey(cfg?:ConfiguredPlacement|null): string | null {
  if (!cfg) return null;
  if (cfg.mode==='pinned' && cfg.gpu_indices.length) return [...cfg.gpu_indices].sort((a,b)=>a-b).join(',');
  if (cfg.mode==='auto') return 'auto';
  return null;
}

function formatModelType(modelType?:string): string | null {
  if (!modelType) return null;
  const key = modelType.toLowerCase();
  if (key==='llm') return 'LLM';
  if (key==='embedding' || key==='embeddings') return 'Embedding';
  if (key==='vlm' || key==='vision') return 'VLM';
  if (key==='tts' || key==='audio') return 'Audio';
  return modelType.toUpperCase();
}

interface CatalogEntry {
  id: string;
  name: string;
  displayName: string;
  installed: boolean;
  hidden: boolean;
  modelType?: string;
}

export function ModelControls({node,jobs,busy,onAction,onPreference,onRename,onHiddenChange,onUnlockHost,compact=false}:{node:Node;jobs:Job[];busy:boolean;onAction:ModelActionHandler;onPreference:RuntimeHandler;onRename:ModelRenameHandler;onHiddenChange:ModelHiddenHandler;onUnlockHost?:(node:Node)=>void;compact?:boolean}) {
  const { t, translateApiText } = useI18n();
  const [selection,setSelection]=useState('');
  const [search,setSearch]=useState('');
  const [statusFilter,setStatusFilter]=useState<'all'|'loaded'|'unloaded'|'hidden'>('all');
  const [typeFilter,setTypeFilter]=useState<string>('all');
  const [dismissedErrorId,setDismissedErrorId]=useState<string|null>(null);
  const [batchMode,setBatchMode]=useState(false);
  const [checkedIds,setCheckedIds]=useState<string[]>([]);
  const { dragItem, startDrag, endDrag } = useModelDrag();

  const formatConfigured = (cfg?:ConfiguredPlacement|null): string => {
    if (!cfg) return t('未取得', 'Unavailable');
    if (cfg.mode==='cpu') return t('僅 CPU', 'CPU Only');
    if (cfg.mode==='pinned' && cfg.gpu_indices.length) return cfg.gpu_indices.map(gpuLabel).join(', ');
    return t('自動分配（全部 GPU）', 'Auto (All GPUs)');
  };

  const formatObserved = (model:LoadedModel): string => {
    if (model.observed_status==='observed' && model.observed_gpus?.length) {
      return model.observed_gpus.map(g=>`${gpuLabel(g.index)} (${formatMb(g.memory_used_mb)})`).join(' · ');
    }
    if (model.observed_status==='cpu_only') return t('僅使用 CPU / 系統記憶體', 'CPU / System RAM only');
    if (model.observed_status==='ambiguous') return t('多實例共用檔案，無法單獨歸屬', 'Shared across instances');
    if (model.observed_status==='unmatched') return t('尚未對應到 NVIDIA 程序', 'Not matched to NVIDIA process');
    if (model.observed_status==='unavailable') return t('無法讀取 NVIDIA 程序', 'NVIDIA telemetry unavailable');
    return t('等待觀測', 'Pending observation');
  };

  const provider=node.default_runtime || 'lmstudio';
  const runtime=node.metrics[provider];
  const isDirectLlama = provider==='lmstudio' && runtime?.backend==='direct_llama';
  const isDiskOnly = provider==='lmstudio' && runtime?.backend==='disk_only';
  const providerName=provider==='lmstudio' ? (isDirectLlama ? t('LM Studio（直載）', 'LM Studio (Direct)') : 'LM Studio') : 'Ollama';
  const known=runtime?.loaded_models_status==='online' && Array.isArray(runtime.loaded_models);
  const residents=runtime?.loaded_models || [];
  const gpus=(node.metrics.gpus || []).filter((g): g is typeof g & {index:number} => typeof g.index==='number');
  const canPickGpu=provider==='lmstudio' && gpus.length>0 && runtime?.gpu_placement_supported!==false;

  const {models, allModels, availableTypes} = useMemo(() => {
    const hiddenSet = new Set(node.hidden_models || []);
    const map = new Map<string, CatalogEntry>();
    for (const m of (node.model_catalog || [])) {
      if (m.runtime !== provider) continue;
      map.set(m.id, {id:m.id, name:m.name, displayName:modelDisplayName(node, m.id, m.name), installed:true, hidden:hiddenSet.has(m.id), modelType:m.model_type});
    }
    for (const m of residents) {
      const id = `${provider}:${m.name}`;
      if (!map.has(id)) map.set(id, {id, name:m.name, displayName:modelDisplayName(node, id, m.name), installed:false, hidden:hiddenSet.has(id)});
    }
    const list = Array.from(map.values());
    const residentNames = new Set(residents.map(r=>r.name));
    list.sort((a,b) => {
      const aLoaded = residentNames.has(a.name) ? 0 : 1;
      const bLoaded = residentNames.has(b.name) ? 0 : 1;
      if (aLoaded !== bLoaded) return aLoaded - bLoaded;
      return a.displayName.localeCompare(b.displayName);
    });
    const typeSet = new Set<string>();
    list.forEach(item => {
      const label = formatModelType(item.modelType);
      if (label) typeSet.add(label);
    });
    return {models:map, allModels:list, availableTypes:Array.from(typeSet)};
  }, [node, provider, residents]);

  const residentMap = useMemo(() => {
    const map = new Map<string, LoadedModel>();
    residents.forEach(r => { if (!map.has(r.name)) map.set(r.name, r); });
    return map;
  }, [residents]);

  const hiddenModelsList = useMemo(() => allModels.filter(m => m.hidden), [allModels]);
  const activeModelsList = useMemo(() => allModels.filter(m => !m.hidden || residentMap.has(m.name)), [allModels, residentMap]);
  const effectiveStatusFilter = statusFilter==='hidden' && hiddenModelsList.length===0 ? 'all' : statusFilter;
  const effectiveTypeFilter = typeFilter==='all' || availableTypes.includes(typeFilter) ? typeFilter : 'all';
  const normalizedSearch = search.trim().toLowerCase();

  const filteredModels = useMemo(() => {
    if (compact) return activeModelsList;
    return allModels.filter(m => {
      const isLoaded = residentMap.has(m.name);
      if (effectiveStatusFilter==='hidden') {
        if (!m.hidden) return false;
      } else {
        if (m.hidden && !isLoaded) return false;
        if (effectiveStatusFilter==='loaded' && !isLoaded) return false;
        if (effectiveStatusFilter==='unloaded' && isLoaded) return false;
      }
      if (effectiveTypeFilter!=='all' && formatModelType(m.modelType)!==effectiveTypeFilter) return false;
      if (normalizedSearch && !m.name.toLowerCase().includes(normalizedSearch) && !m.displayName.toLowerCase().includes(normalizedSearch)) return false;
      return true;
    });
  }, [activeModelsList, allModels, compact, effectiveStatusFilter, effectiveTypeFilter, normalizedSearch, residentMap]);

  const selected=models.get(selection) && (!models.get(selection)!.hidden || residentMap.has(models.get(selection)!.name))
    ? models.get(selection)
    : models.get(`${provider}:${residents[0]?.name}`) || activeModelsList[0];
  const visibleModels=compact ? (selected ? [selected] : []) : filteredModels;
  const loadedVisible = compact || effectiveStatusFilter==='hidden' ? [] : visibleModels.filter(m => residentMap.has(m.name));
  const unloadedVisible = compact || effectiveStatusFilter==='hidden' ? [] : visibleModels.filter(m => !residentMap.has(m.name));

  const visibleIdSet = useMemo(() => new Set(visibleModels.map(m => m.id)), [visibleModels]);
  const activeChecked = useMemo(() => checkedIds.filter(id => visibleIdSet.has(id)), [checkedIds, visibleIdSet]);
  const allChecked = visibleModels.length > 0 && activeChecked.length === visibleModels.length;

  const toggleChecked = (id: string) => {
    setCheckedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };
  const toggleAll = () => {
    setCheckedIds(allChecked ? [] : visibleModels.map(m => m.id));
  };
  const applyBatchHide = () => {
    if (!activeChecked.length) return;
    if (effectiveStatusFilter === 'hidden') {
      onHiddenChange(node, [], activeChecked);
    } else {
      onHiddenChange(node, activeChecked, []);
    }
    setCheckedIds([]);
  };

  const latest=jobs.find(j=>j.model.startsWith(provider+':') && j.parameters.model_action && j.parameters.target_node_id===node.id);
  const active=jobs.find(j=>(j.node_id===node.id || j.parameters.target_node_id===node.id) && (['running','cancelling','unknown'].includes(j.status) || (j.status==='queued' && !!j.parameters.model_action)));
  const syncing=latest?.status==='completed' && !!latest.finished_at && latest.finished_at>node.last_seen;
  const disabled=busy || !!active || syncing || node.status!=='online' || !known || !node.capabilities.includes(`${provider}.model_control`);

  const hasFilters = !compact && (normalizedSearch!=='' || effectiveStatusFilter!=='all' || effectiveTypeFilter!=='all');
  const loadedCount = activeModelsList.filter(m => residentMap.has(m.name)).length;
  const unloadedCount = activeModelsList.length - loadedCount;
  const hiddenCount = hiddenModelsList.length;

  const runtimes: {id:'lmstudio'|'ollama'; label:string; online:boolean; loaded:number | null}[] = [
    {
      id: 'lmstudio',
      label: 'LM Studio',
      online: node.metrics.lmstudio?.loaded_models_status === 'online',
      loaded: node.metrics.lmstudio?.loaded_models_status === 'online' && Array.isArray(node.metrics.lmstudio.loaded_models)
        ? node.metrics.lmstudio.loaded_models.length : null,
    },
    {
      id: 'ollama',
      label: 'Ollama',
      online: node.metrics.ollama?.loaded_models_status === 'online',
      loaded: node.metrics.ollama?.loaded_models_status === 'online' && Array.isArray(node.metrics.ollama.loaded_models)
        ? node.metrics.ollama.loaded_models.length : null,
    },
  ];

  const statusBanner = active
    ? {
        id: active.id,
        dismissible: false,
        tone: active.status==='unknown' ? 'warning' : 'info',
        text: active.status==='unknown'
          ? t('上次操作狀態待確認，已保留主機工作槽。', 'Previous operation state unconfirmed; host slot reserved.')
          : active.parameters.model_action
            ? t(`${active.name}：${active.status==='queued' ? '等待 Agent 派送' : '正在處理，請稍候'}。`, `${active.name}: ${active.status==='queued' ? 'waiting for agent dispatch' : 'in progress, please wait'}.`)
            : t('主機正在執行推論，完成後即可操作模型。', 'Host is running inference; model controls unlock after completion.'),
      }
    : syncing
      ? { id: 'syncing', dismissible: false, tone: 'info', text: t('正在同步模型最新狀態…', 'Syncing latest model residency…') }
      : latest?.status==='failed' && dismissedErrorId !== latest.id
        ? { id: latest.id, dismissible: true, tone: 'error', text: t(`操作失敗：${latest.error || '請查看任務紀錄。'}`, `Operation failed: ${latest.error ? translateApiText(latest.error) : 'see job log.'}`) }
        : null;

  const renderModelRow = (model: CatalogEntry) => {
    const resident=residentMap.get(model.name);
    const operation=active?.model===model.id ? active.parameters.model_action : undefined;
    const activeTargetKey=active?.model===model.id && operation==='load'
      ? (active.parameters.gpu_indices?.length ? [...active.parameters.gpu_indices].sort((a,b)=>a-b).join(',') : 'auto')
      : null;
    const currentKey=resident ? configuredTargetKey(resident.configured_placement) : null;
    const typeTag=formatModelType(model.modelType);
    const isBusyRow=!!operation;
    const isChecked=activeChecked.includes(model.id);
    const canDrag=!compact && effectiveStatusFilter!=='hidden' && canPickGpu && !disabled && provider==='lmstudio';
    const isSelfDragging=dragItem?.nodeId===node.id && dragItem.modelId===model.id;
    const primarySourceGpu = resident?.observed_gpus?.[0]?.index
      ?? (resident?.configured_placement?.mode === 'pinned' ? resident.configured_placement.gpu_indices[0] ?? null : null);

    return <li
      key={model.id}
      draggable={canDrag}
      className={`gcc-model-item${resident ? ' is-resident' : ''}${isBusyRow ? ' is-busy' : ''}${isChecked ? ' is-checked' : ''}${canDrag ? ' is-draggable-row' : ''}${isSelfDragging ? ' is-dragging' : ''}`}
      title={canDrag ? t(`拖曳 ${model.displayName} 至右側 GPU 卡片即可指定載入或搬移`, `Drag ${model.displayName} to a GPU card on the right to load or move`) : undefined}
      onDragStart={e => {
        if (!canDrag) return;
        startDrag(e, buildLmStudioDragPayload(node, model.name, resident, primarySourceGpu));
      }}
      onDragEnd={endDrag}
    >
      {canDrag && <span className="gcc-drag-handle" aria-hidden="true"><DotsSixVertical size={15}/></span>}
      {batchMode && !compact && <label className="gcc-batch-check">
        <input type="checkbox" checked={isChecked} onChange={()=>toggleChecked(model.id)} aria-label={t(`選取 ${model.displayName}`, `Select ${model.displayName}`)}/>
      </label>}
      <div className="gcc-control-model-info">
        <div className="gcc-model-title-row">
          <span className={`gcc-model-dot${resident ? ' is-loaded' : ''}`} aria-hidden="true"/>
          <EditableModelName
            displayName={model.displayName}
            rawName={model.name}
            disabled={busy}
            onSave={alias => onRename(node, model.id, alias)}
          />
          {typeTag && <span className="gcc-model-type-tag">{typeTag}</span>}
        </div>
        {resident ? <div className="gcc-placement-meta">
          {provider==='lmstudio' && <span className="gcc-placement-chip"><em>{t('指定配置', 'Configured')}</em><span>{formatConfigured(resident.configured_placement)}</span></span>}
          <span className="gcc-placement-chip"><em>{t('實際觀測', 'Observed')}</em><span>{formatObserved(resident)}</span></span>
          {provider==='ollama' && <span className="gcc-placement-chip"><em>{t('執行器回報', 'Runtime')}</em><span>{t(`顯存 ${size(resident.vram_bytes)} · 大小 ${size(resident.size_bytes)}`, `VRAM ${size(resident.vram_bytes)} · Size ${size(resident.size_bytes)}`)}</span></span>}
        </div> : <span className="gcc-model-idle-label">{model.hidden ? t('已隱藏（未刪除本機檔案）', 'Hidden (local file preserved)') : known ? t('未載入 · 點選右側目標直接載入', 'Not loaded · pick a target GPU to load') : t('載入狀態未知', 'Load state unknown')}</span>}
      </div>
      <div className="gcc-control-actions">
        {effectiveStatusFilter === 'hidden' ? (
          <button
            type="button"
            className="gcc-button gcc-btn-sm"
            disabled={busy}
            onClick={()=>onHiddenChange(node, [], [model.id])}
          ><Eye size={14}/>{t('恢復顯示', 'Unhide')}</button>
        ) : canPickGpu ? <>
          <div className="gcc-gpu-seg" role="group" aria-label={t(`${model.displayName} 目標 GPU`, `Target GPU for ${model.displayName}`)}>
            <button
              type="button"
              className={`gcc-gpu-seg-btn${resident && currentKey==='auto' ? ' is-active' : ''}`}
              aria-pressed={!!resident && currentKey==='auto'}
              disabled={disabled || (!!resident && currentKey==='auto')}
              title={t('自動分配全部 GPU', 'Auto-distribute across all GPUs')}
              onClick={()=>onAction(node, model.id, 'load', null, !!resident)}
            >
              {activeTargetKey==='auto' && <CircleNotch size={12} className="gcc-spin"/>}
              {t('自動', 'Auto')}
            </button>
            {gpus.map(g=>{
              const key=String(g.index);
              const isCurrent=!!resident && currentKey===key;
              const isLoadingThis=activeTargetKey===key;
              const freeGb=g.memory_total_mb!=null && g.memory_used_mb!=null ? Math.max(0,(g.memory_total_mb-g.memory_used_mb)/1024).toFixed(1) : null;
              return <button
                key={g.index}
                type="button"
                className={`gcc-gpu-seg-btn${isCurrent ? ' is-active' : ''}`}
                aria-pressed={isCurrent}
                disabled={disabled || isCurrent}
                title={`${gpuLabel(g.index)} · ${shortGpuName(g.name)}${freeGb!==null ? t(`（空閒約 ${freeGb} GB）`, ` (~${freeGb} GB free)`) : ''}`}
                onClick={()=>onAction(node, model.id, 'load', [g.index], !!resident)}
              >
                {isLoadingThis && <CircleNotch size={12} className="gcc-spin"/>}
                {shortGpuLabel(g.index)}
              </button>;
            })}
          </div>
          {resident && <button
            type="button"
            className="gcc-button gcc-button--unload gcc-btn-sm"
            disabled={disabled}
            aria-label={t(`卸載 ${model.displayName}`, `Unload ${model.displayName}`)}
            onClick={()=>onAction(node, model.id, 'unload')}
          >
            {operation==='unload' ? <CircleNotch size={14} className="gcc-spin"/> : <Eject size={14}/>}
            {operation==='unload' ? t('卸載中', 'Unloading') : t('卸載', 'Unload')}
          </button>}
        </> : <button
          type="button"
          className={`gcc-button${resident ? ' gcc-button--unload' : ' gcc-button--load'}`}
          disabled={disabled}
          aria-label={`${resident ? t('卸載', 'Unload') : t('載入', 'Load')} ${model.displayName}`}
          onClick={()=>onAction(node, model.id, resident ? 'unload' : 'load')}
        >
          {operation ? <CircleNotch size={16} className="gcc-spin"/> : resident ? <Eject size={16}/> : <DownloadSimple size={16}/>}
          {operation ? (active?.status==='queued' ? t('等待派送', 'Queued') : operation==='load' ? t('載入中', 'Loading') : t('卸載中', 'Unloading')) : resident ? t('卸載', 'Unload') : t('載入', 'Load')}
        </button>}
      </div>
    </li>;
  };

  return <section className={`gcc-loaded-models${compact ? ' is-compact' : ' is-full'}`} aria-label={t(`${node.name} 模型控制`, `${node.name} model controls`)}>
    <div className="gcc-loaded-heading">
      <span className="gcc-loaded-icon" aria-hidden="true"><Stack size={20}/></span>
      <div>
        <h4>{t('模型載入控制', 'Model Residency Control')}</h4>
        <p>{node.status==='offline' ? t('主機離線 · 以下為最後回報狀態', 'Host offline · showing last reported state') : t('點選 GPU 按鈕或拖曳模型列至右側 GPU 卡片即可載入／換卡', 'Click a GPU button or drag any model row to a GPU card to load or relocate')}</p>
      </div>
      <span className={`gcc-badge${known && residents.length > 0 ? ' gcc-badge--online' : ''}`}>
        {known ? t(`${residents.length} / ${activeModelsList.length} 已載入`, `${residents.length} / ${activeModelsList.length} Loaded`) : t('— 已載入', '— Loaded')}
      </span>
    </div>

    {node.mode!=='demo' && (compact ? <div className="gcc-control-selectors">
      <label className="gcc-runtime-select"><span>{t('預設執行器', 'Default Runtime')}</span><select value={provider} disabled={busy} onChange={e=>onPreference(node,e.target.value as 'lmstudio'|'ollama')}><option value="lmstudio">LM Studio</option><option value="ollama">Ollama</option></select></label>
      {!!activeModelsList.length && <label className="gcc-runtime-select"><span>{t('選擇模型', 'Select Model')}</span><select value={selected?.id || ''} onChange={e=>setSelection(e.target.value)}>{activeModelsList.map(m=><option key={m.id} value={m.id}>{m.displayName}</option>)}</select></label>}
    </div> : <div className="gcc-control-config">
      <div className="gcc-config-block">
        <div className="gcc-config-label-row">
          <span className="gcc-config-label" id={`runtime-label-${node.id}`}>{t('預設執行器', 'Default Runtime')}</span>
          <span className="gcc-config-hint">{t('儲存於此主機，推論表單亦優先使用', 'Persisted per host and prioritized in new jobs')}</span>
        </div>
        <div className="gcc-runtime-switch" role="radiogroup" aria-labelledby={`runtime-label-${node.id}`}>
          {runtimes.map(rt => {
            const activeRt = provider === rt.id;
            return <button
              key={rt.id}
              type="button"
              role="radio"
              aria-checked={activeRt}
              disabled={busy}
              className={`gcc-runtime-option${activeRt ? ' is-selected' : ''}`}
              onClick={()=>{ if (!activeRt) { setCheckedIds([]); onPreference(node, rt.id); } }}
            >
              <span className={`gcc-runtime-dot${rt.online ? ' is-online' : ''}`} aria-hidden="true"/>
              <span className="gcc-runtime-name">{rt.label}</span>
              <span className="gcc-runtime-count">{rt.loaded !== null ? t(`${rt.loaded} 載入`, `${rt.loaded} loaded`) : t('離線', 'Offline')}</span>
            </button>;
          })}
        </div>
      </div>
    </div>)}

    {node.mode==='demo' ? <p className="gcc-muted">{t('模擬主機不提供真實模型控制。', 'Simulated hosts do not support real model controls.')}</p> : <>
      {node.auth_required && !node.auth_unlocked && <div className="gcc-auth-notice" role="status">
        <span><LockSimple size={15}/>{t('此主機已啟用操作密碼保護，載入、卸載或拖曳切換模型前須先驗證密碼。', 'Password protection is active on this host. Verify password before loading, unloading, or moving models.')}</span>
        {onUnlockHost && <button type="button" className="gcc-button gcc-btn-sm gcc-auth-btn is-locked" disabled={busy} onClick={()=>onUnlockHost(node)}>{t('立即解鎖', 'Unlock')}</button>}
      </div>}
      {!known && <p className="gcc-muted gcc-status-empty">{runtime?.loaded_models_status==='unavailable' ? t('無法取得執行器狀態，等待重新連線。', 'Runtime unreachable — waiting to reconnect.') : t('等待 Agent 回報模型載入狀態。', 'Waiting for host agent to report loaded models.')}</p>}

      {statusBanner && <div role="status" className={`gcc-model-op-banner gcc-model-op-banner--${statusBanner.tone}`}>
        {statusBanner.tone==='info' ? <CircleNotch size={15} className="gcc-spin"/> : <WarningCircle size={15}/>}
        <span>{statusBanner.text}</span>
        {statusBanner.dismissible && <button type="button" className="gcc-icon-button gcc-banner-close" aria-label={t('關閉訊息', 'Dismiss message')} onClick={()=>setDismissedErrorId(statusBanner.id)}><X size={14}/></button>}
      </div>}

      {compact && known && !residents.length && <p className="gcc-muted">{t('目前沒有常駐模型，點選下方 GPU 按鈕即可載入。', 'No resident models. Click a GPU button below to load one.')}</p>}
      {compact && !!residents.length && <div className="gcc-resident-chips"><span>{t('目前載入', 'Loaded')}</span>{residents.map((m,i)=><span className="gcc-resident-chip" key={`${m.name}-${i}`}><code>{modelDisplayName(node, `${provider}:${m.name}`, m.name)}</code>{m.observed_status==='observed' && m.observed_gpus?.length ? <b>{m.observed_gpus.map(g=>`${shortGpuLabel(g.index)} ${formatMb(g.memory_used_mb)}`).join(' + ')}</b> : null}</span>)}</div>}

      {!compact && allModels.length > 0 && <div className="gcc-model-toolbar">
        <div className="gcc-search gcc-model-search">
          <MagnifyingGlass size={16} aria-hidden="true"/>
          <input
            type="search"
            value={search}
            onChange={e=>setSearch(e.target.value)}
            placeholder={t(`搜尋 ${providerName} 模型名稱…`, `Search ${providerName} models…`)}
            aria-label={t(`搜尋 ${node.name} ${providerName} 模型`, `Search ${node.name} ${providerName} models`)}
          />
          {search && <button type="button" className="gcc-icon-button" aria-label={t('清除搜尋', 'Clear search')} onClick={()=>setSearch('')}><X size={15}/></button>}
        </div>
        <div className="gcc-model-filters" role="group" aria-label={t('依狀態篩選模型', 'Filter models by status')}>
          {([
            {id:'all', label:t('全部', 'All'), count:activeModelsList.length},
            {id:'loaded', label:t('已載入', 'Loaded'), count:loadedCount},
            {id:'unloaded', label:t('未載入', 'Unloaded'), count:unloadedCount},
          ] as const).map(tab => <button
            key={tab.id}
            type="button"
            aria-pressed={effectiveStatusFilter===tab.id}
            className={`gcc-filter-pill${effectiveStatusFilter===tab.id ? ' is-active' : ''}`}
            onClick={()=>{ setStatusFilter(tab.id); setCheckedIds([]); }}
          >{tab.label}<span>{tab.count}</span></button>)}
          {hiddenCount > 0 && <button
            type="button"
            aria-pressed={effectiveStatusFilter==='hidden'}
            className={`gcc-filter-pill${effectiveStatusFilter==='hidden' ? ' is-active' : ''}`}
            onClick={()=>{ setStatusFilter(effectiveStatusFilter==='hidden' ? 'all' : 'hidden'); setCheckedIds([]); }}
          ><EyeSlash size={13}/>{t('已隱藏', 'Hidden')}<span>{hiddenCount}</span></button>}
          {availableTypes.length > 1 && <span className="gcc-filter-divider" aria-hidden="true"/>}
          {availableTypes.length > 1 && availableTypes.map(typeItem => <button
            key={typeItem}
            type="button"
            aria-pressed={effectiveTypeFilter===typeItem}
            className={`gcc-filter-pill${effectiveTypeFilter===typeItem ? ' is-active' : ''}`}
            onClick={()=>setTypeFilter(effectiveTypeFilter===typeItem ? 'all' : typeItem)}
          >{typeItem}</button>)}
          <button
            type="button"
            aria-pressed={batchMode}
            className={`gcc-filter-pill gcc-batch-toggle${batchMode ? ' is-active' : ''}`}
            onClick={()=>{ setBatchMode(v => !v); setCheckedIds([]); }}
          ><CheckSquare size={14}/>{batchMode ? t('完成批次', 'Done') : t('批次管理', 'Batch')}</button>
        </div>
      </div>}

      {!compact && batchMode && <div className="gcc-batch-bar" role="region" aria-label={t('批次隱藏操作列', 'Batch hide toolbar')}>
        <button type="button" className="gcc-button gcc-btn-sm" onClick={toggleAll} disabled={!visibleModels.length}>
          {allChecked ? t('取消全選', 'Deselect All') : t('全選目前列表', 'Select All')}
        </button>
        <span className="gcc-batch-count">{t('已選', 'Selected')} <strong>{activeChecked.length}</strong> / {visibleModels.length}</span>
        <div className="gcc-batch-actions">
          <button
            type="button"
            className={`gcc-button gcc-btn-sm${effectiveStatusFilter==='hidden' ? ' gcc-button--primary' : ' gcc-button--unload'}`}
            disabled={busy || !activeChecked.length}
            onClick={applyBatchHide}
          >
            {effectiveStatusFilter==='hidden' ? <Eye size={14}/> : <EyeSlash size={14}/>}
            {effectiveStatusFilter==='hidden' ? t(`恢復顯示 (${activeChecked.length})`, `Unhide (${activeChecked.length})`) : t(`隱藏所選 (${activeChecked.length})`, `Hide Selected (${activeChecked.length})`)}
          </button>
        </div>
      </div>}

      {compact ? (
        <ul className="gcc-control-models">{visibleModels.map(renderModelRow)}</ul>
      ) : effectiveStatusFilter === 'hidden' ? (
        <ul className="gcc-control-models">{visibleModels.map(renderModelRow)}</ul>
      ) : (
        <>
          {loadedVisible.length > 0 && <div className="gcc-model-group">
            <div className="gcc-model-group-head">
              <span>{t('已載入常駐', 'Loaded & Resident')}</span>
              <span className="gcc-model-group-count">{loadedVisible.length}</span>
            </div>
            <ul className="gcc-control-models gcc-control-models--loaded">{loadedVisible.map(renderModelRow)}</ul>
          </div>}

          {unloadedVisible.length > 0 && <div className="gcc-model-group">
            {(loadedVisible.length > 0 || effectiveStatusFilter==='all') && <div className="gcc-model-group-head">
              <span>{t('可載入模型', 'Available Models')}</span>
              <span className="gcc-model-group-count">{unloadedVisible.length}</span>
            </div>}
            <ul className="gcc-control-models">{unloadedVisible.map(renderModelRow)}</ul>
          </div>}

          {allModels.length > 0 && visibleModels.length === 0 && <div className="gcc-model-empty-filter">
            <p>{t('沒有符合目前篩選條件的模型。', 'No models match your current filters.')}</p>
            {hasFilters && <button type="button" className="gcc-button gcc-button--quiet" onClick={()=>{setSearch('');setStatusFilter('all');setTypeFilter('all');}}>{t('清除篩選', 'Clear Filters')}</button>}
          </div>}
        </>
      )}

      {!models.size && <p className="gcc-muted gcc-status-empty">{t('尚無已安裝模型，請確認執行器已啟動並安裝模型。', 'No installed models found. Ensure the runtime is online with models installed.')}</p>}
    </>}

    <p className="gcc-loaded-note"><Info size={14}/><span>{node.maintenance ? t('主機已停止接單，恢復後才能操作。', 'Host is in maintenance mode; resume scheduling to control models.') : isDiskOnly ? t(`已從磁碟目錄${runtime?.models_dir ? `（${runtime.models_dir}）` : ''}讀取模型清單；LM Studio 服務未啟動且未偵測到本地 llama-server，啟動後即可載入。`, `Catalog read from disk${runtime?.models_dir ? ` (${runtime.models_dir})` : ''}; start LM Studio or configure llama-server to load models.`) : !node.capabilities.includes(`${provider}.model_control`) ? t('請更新主機 Agent 以啟用模型控制。', 'Update the host agent to enable model controls.') : isDirectLlama ? t(`已直接讀取磁碟目錄${runtime?.models_dir ? `（${runtime.models_dir}）` : ''}，不需開啟 LM Studio App，改由本地 llama-server 直載至指定 GPU；批次隱藏僅從列表移除顯示，不會刪除硬碟檔案。`, `Reading GGUF catalog directly from disk${runtime?.models_dir ? ` (${runtime.models_dir})` : ''} via local llama-server with per-GPU pinning; hiding only removes models from this list.`) : provider==='lmstudio' ? t(`${providerName} 透過官方 SDK 支援逐模型指定 GPU；批次隱藏僅從列表移除顯示，不會刪除硬碟檔案。`, `${providerName} supports per-model GPU pinning via SDK; hiding only removes models from this list without deleting files.`) : t(`${providerName} 目前透過單一服務連線，不支援逐模型指定 GPU；批次隱藏僅從列表移除顯示，不會刪除硬碟檔案。`, `${providerName} connects via a single endpoint and does not support per-model GPU pinning; hiding only removes models from this list.`)}</span></p>
  </section>;
}
