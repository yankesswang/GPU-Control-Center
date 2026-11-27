import {useMemo, useState} from 'react';
import { CheckSquare, CircleNotch, Cpu, DotsSixVertical, DownloadSimple, Eject, Eye, EyeSlash, FlowArrow, Queue, Stack, WarningCircle, X } from '@phosphor-icons/react';
import { formatBytes, modelDisplayName, type Job, type Node } from '../api';
import { useI18n } from '../i18n';
import type { ModelActionHandler, ModelHiddenHandler, ModelRenameHandler } from './ModelControls';
import { Badge, EditableModelName } from './ui';
import { buildComfyDragPayload, useModelDrag } from './modelDrag';

const NON_MODEL_FOLDERS = new Set(['custom_nodes', 'configs', 'datasets']);
const LOADABLE_FOLDERS = new Set(['diffusion_models', 'unet_gguf', 'checkpoints']);
const PRIORITY_FOLDERS = ['diffusion_models', 'checkpoints', 'unet_gguf', 'text_encoders', 'clip_gguf', 'vae', 'loras', 'controlnet', 'upscale_models'];
const gpuLabel = (idx: number) => `GPU ${String(idx).padStart(2, '0')}`;
const shortGpuLabel = (idx: number) => `GPU ${idx}`;
const shortGpuName = (name: string) => name.replace(/^NVIDIA\s+(GeForce\s+)?/i, '');

function formatDeviceName(raw: string): string {
  const match = raw.match(/^cuda:(\d+)\s+(.+?)(?:\s*:\s*(.+))?$/i);
  if (!match) return raw;
  const [, idx, gpuName] = match;
  return `${gpuLabel(Number(idx))} · ${shortGpuName(gpuName)}`;
}

export function ComfyPanel({node,jobs,busy,onAction,onRename,onHiddenChange}:{node:Node;jobs:Job[];busy:boolean;onAction:ModelActionHandler;onRename:ModelRenameHandler;onHiddenChange:ModelHiddenHandler}) {
  const { t, comfyRoleLabel, translateApiText } = useI18n();
  const [dismissedErrorId, setDismissedErrorId] = useState<string | null>(null);
  const [batchMode, setBatchMode] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const { dragItem, startDrag, endDrag } = useModelDrag();

  const formatTargetDevice = (device?: string | null): string => {
    if (!device || device === 'default') return t('預設裝置', 'Default Device');
    const match = device.match(/^gpu:(\d+)$/);
    return match ? gpuLabel(Number(match[1])) : device;
  };

  const comfy=node.metrics.comfyui;
  const hiddenSet = useMemo(() => new Set(node.hidden_models || []), [node.hidden_models]);
  const allGroups=comfy?.model_groups || [];
  const sortFolders=(a:{folder:string},b:{folder:string})=>{
    const ai=PRIORITY_FOLDERS.indexOf(a.folder);
    const bi=PRIORITY_FOLDERS.indexOf(b.folder);
    if (ai!==-1 || bi!==-1) return (ai===-1 ? 999 : ai)-(bi===-1 ? 999 : bi);
    return a.folder.localeCompare(b.folder);
  };
  const modelGroups=allGroups.filter(g=>!NON_MODEL_FOLDERS.has(g.folder));
  const diffusionGroup=modelGroups.find(g=>g.folder==='diffusion_models');
  const otherModelGroups=modelGroups.filter(g=>g.folder!=='diffusion_models');
  const populatedOthers=otherModelGroups.filter(g=>g.status!=='online' || (g.count ?? g.files.length)>0).sort(sortFolders);
  const emptyModelFolders=otherModelGroups.filter(g=>g.status==='online' && (g.count ?? g.files.length)===0).length;
  const auxGroups=allGroups.filter(g=>NON_MODEL_FOLDERS.has(g.folder) && (g.count ?? g.files.length)>0).sort(sortFolders);

  const visibleDiffusionFiles = (diffusionGroup?.files || []).filter(f => !hiddenSet.has(`comfyui:${f}`));
  const hiddenLoadableFiles = useMemo(() => {
    const out: {folder: string; file: string; id: string}[] = [];
    for (const g of modelGroups) {
      if (!LOADABLE_FOLDERS.has(g.folder)) continue;
      for (const f of g.files || []) {
        const id = `comfyui:${f}`;
        if (hiddenSet.has(id)) out.push({folder: g.folder, file: f, id});
      }
    }
    return out;
  }, [hiddenSet, modelGroups]);

  const effectiveShowHidden = showHidden && hiddenLoadableFiles.length > 0;
  const batchScopeIds = effectiveShowHidden
    ? hiddenLoadableFiles.map(x => x.id)
    : visibleDiffusionFiles.map(f => `comfyui:${f}`);
  const batchScopeSet = useMemo(() => new Set(batchScopeIds), [batchScopeIds]);
  const activeChecked = useMemo(() => checkedIds.filter(id => batchScopeSet.has(id)), [checkedIds, batchScopeSet]);
  const allChecked = batchScopeIds.length > 0 && activeChecked.length === batchScopeIds.length;

  const toggleChecked = (id: string) => setCheckedIds(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  const toggleAll = () => setCheckedIds(allChecked ? [] : batchScopeIds);
  const applyBatch = () => {
    if (!activeChecked.length) return;
    if (effectiveShowHidden) onHiddenChange(node, [], activeChecked);
    else onHiddenChange(node, activeChecked, []);
    setCheckedIds([]);
  };

  const otherModelsCount=populatedOthers.reduce((sum,g)=>{
    if (g.status!=='online') return sum;
    if (LOADABLE_FOLDERS.has(g.folder)) return sum + (g.files || []).filter(f => !hiddenSet.has(`comfyui:${f}`)).length;
    return sum + (g.count ?? g.files.length);
  },0);

  const gpus=(node.metrics.gpus || []).filter((g): g is typeof g & {index:number} => typeof g.index==='number');
  const canPickGpu=gpus.length>0 && comfy?.gpu_placement_supported!==false;

  const latest=jobs.find(j=>j.model.startsWith('comfyui:') && j.parameters.model_action && j.parameters.target_node_id===node.id);
  const lastCompleted=jobs.find(j=>j.model.startsWith('comfyui:') && j.parameters.model_action && j.parameters.target_node_id===node.id && j.status==='completed');
  const active=jobs.find(j=>(j.node_id===node.id || j.parameters.target_node_id===node.id) && (['running','cancelling','unknown'].includes(j.status) || (j.status==='queued' && !!j.parameters.model_action)));
  const preloadedModel=lastCompleted?.parameters.model_action==='load' ? lastCompleted.model.slice('comfyui:'.length) : null;
  const preloadedDevice=lastCompleted?.parameters.model_action==='load' && typeof lastCompleted.result?.target_device==='string' ? lastCompleted.result.target_device : null;
  const preloadedKey=preloadedDevice && preloadedDevice.startsWith('gpu:') ? preloadedDevice.slice(4) : 'default';
  const activeModels=comfy?.active_models || [];
  const activeModelNames=useMemo(()=>new Set(activeModels.map(m=>m.name)), [activeModels]);
  const isQueueRunning=(comfy?.running?.length || 0) > 0;
  const telemetryOnline=comfy?.loaded_models_telemetry === 'online';
  const activeModelsTitle=isQueueRunning
    ? (telemetryOnline ? t('目前執行使用模型（ComfyUI 即時顯存）', 'Active Models (ComfyUI Live VRAM)') : t('目前執行使用模型', 'Active Models'))
    : telemetryOnline
      ? t('目前常駐快取模型（ComfyUI 即時顯存）', 'Cached Models (ComfyUI Live VRAM)')
      : comfy?.active_models_source === 'process'
        ? t('目前常駐快取模型（實際觀測）', 'Cached Models (Observed)')
        : t('最近執行使用模型', 'Recently Used Models');

  const canControl=node.status==='online' && !node.maintenance && comfy?.status==='online' && node.capabilities.includes('comfyui.model_control');
  const disabled=busy || !!active || !canControl;
  const isUnloadingAll=active?.model==='comfyui:*' && active.parameters.model_action==='unload';

  const statusBanner = active?.model.startsWith('comfyui:')
    ? { id: active.id, dismissible: false, tone: 'info' as const, text: t(`${active.name}：${active.status==='queued' ? '等待 Agent 派送' : '正在處理，請稍候'}。`, `${active.name}: ${active.status==='queued' ? 'waiting for agent dispatch' : 'in progress, please wait'}.`) }
    : latest?.status==='failed' && dismissedErrorId !== latest.id
      ? { id: latest.id, dismissible: true, tone: 'error' as const, text: t(`ComfyUI 操作失敗：${latest.error || '請查看任務紀錄。'}`, `ComfyUI operation failed: ${latest.error ? translateApiText(latest.error) : 'see job log.'}`) }
      : null;

  const renderLoadableRow = (file: string, allowCheck = false, hiddenMode = false) => {
    const modelId = `comfyui:${file}`;
    const displayName = modelDisplayName(node, modelId, file);
    const isPreloaded = preloadedModel === file;
    const isObservedActive = activeModelNames.has(file);
    const isLoaded = isPreloaded || isObservedActive;
    const operation = active?.model === modelId ? active.parameters.model_action : undefined;
    const activeTargetKey = active?.model === modelId && operation === 'load'
      ? (active.parameters.gpu_indices?.length ? String(active.parameters.gpu_indices[0]) : 'default')
      : null;
    const shouldReplace = Boolean(preloadedModel || activeModels.length);
    const isChecked = activeChecked.includes(modelId);
    const statusTag = isObservedActive && isQueueRunning
      ? `${t('執行中', 'Running')}${isPreloaded ? ` · ${formatTargetDevice(preloadedDevice)}` : ''}`
      : isPreloaded
        ? `${t('已預載快取', 'Preloaded')} · ${formatTargetDevice(preloadedDevice)}`
        : isObservedActive
          ? t('已載入快取', 'Cached')
          : null;

    const canDrag = !hiddenMode && canPickGpu && !disabled;
    const isSelfDragging = dragItem?.nodeId === node.id && dragItem.modelId === modelId;
    const sourceGpuIdx = isPreloaded && preloadedKey !== 'default' ? Number(preloadedKey) : null;

    return <li
      key={file}
      draggable={canDrag}
      className={`gcc-comfy-model-row${isLoaded ? ' is-preloaded' : ''}${isChecked ? ' is-checked' : ''}${canDrag ? ' is-draggable-row' : ''}${isSelfDragging ? ' is-dragging' : ''}`}
      title={canDrag ? t(`拖曳 ${displayName} 至 GPU 卡片以指定裝置預載`, `Drag ${displayName} to a GPU card to preload on that device`) : undefined}
      onDragStart={e => {
        if (!canDrag) return;
        startDrag(e, buildComfyDragPayload(node, file, jobs, sourceGpuIdx));
      }}
      onDragEnd={endDrag}
    >
      {canDrag && <span className="gcc-drag-handle" aria-hidden="true"><DotsSixVertical size={14}/></span>}
      {batchMode && allowCheck && <label className="gcc-batch-check">
        <input type="checkbox" checked={isChecked} onChange={()=>toggleChecked(modelId)} aria-label={t(`選取 ${displayName}`, `Select ${displayName}`)}/>
      </label>}
      <div className="gcc-comfy-model-meta">
        <EditableModelName
          displayName={displayName}
          rawName={file}
          disabled={busy}
          onSave={alias => onRename(node, modelId, alias)}
        />
        {statusTag && <span className="gcc-comfy-preloaded-tag">{statusTag}</span>}
      </div>
      <div className="gcc-control-actions">
        {hiddenMode ? (
          <button type="button" className="gcc-button gcc-btn-sm" disabled={busy} onClick={()=>onHiddenChange(node, [], [modelId])}>
            <Eye size={14}/>{t('恢復顯示', 'Unhide')}
          </button>
        ) : canPickGpu ? <>
          <div className="gcc-gpu-seg" role="group" aria-label={t(`${displayName} 目標 GPU`, `Target GPU for ${displayName}`)}>
            <button
              type="button"
              className={`gcc-gpu-seg-btn${isPreloaded && preloadedKey==='default' ? ' is-active' : ''}`}
              aria-pressed={isPreloaded && preloadedKey==='default'}
              disabled={disabled || (isPreloaded && preloadedKey==='default')}
              title={t('使用 ComfyUI 預設裝置', 'Use ComfyUI default device')}
              onClick={()=>onAction(node, modelId, 'load', null, shouldReplace)}
            >
              {activeTargetKey==='default' && <CircleNotch size={12} className="gcc-spin"/>}
              {t('預設', 'Default')}
            </button>
            {gpus.map(g=>{
              const key=String(g.index);
              const isCurrent=isPreloaded && preloadedKey===key;
              const isLoadingThis=activeTargetKey===key;
              const freeGb=g.memory_total_mb!=null && g.memory_used_mb!=null ? Math.max(0,(g.memory_total_mb-g.memory_used_mb)/1024).toFixed(1) : null;
              return <button
                key={g.index}
                type="button"
                className={`gcc-gpu-seg-btn${isCurrent ? ' is-active' : ''}`}
                aria-pressed={isCurrent}
                disabled={disabled || isCurrent}
                title={`${gpuLabel(g.index)} · ${shortGpuName(g.name)}${freeGb!==null ? t(`（空閒約 ${freeGb} GB）`, ` (~${freeGb} GB free)`) : ''}`}
                onClick={()=>onAction(node, modelId, 'load', [g.index], shouldReplace)}
              >
                {isLoadingThis && <CircleNotch size={12} className="gcc-spin"/>}
                {shortGpuLabel(g.index)}
              </button>;
            })}
          </div>
          {isLoaded && <button
            type="button"
            className="gcc-button gcc-button--unload gcc-btn-sm"
            disabled={disabled}
            aria-label={t(`卸載 ${displayName}`, `Unload ${displayName}`)}
            onClick={()=>onAction(node, modelId, 'unload')}
          >
            {operation==='unload' ? <CircleNotch size={14} className="gcc-spin"/> : <Eject size={14}/>}
            {operation==='unload' ? t('卸載中', 'Unloading') : t('卸載', 'Unload')}
          </button>}
        </> : <button
          type="button"
          className={`gcc-button${isLoaded ? ' gcc-button--unload' : ' gcc-button--load'}`}
          disabled={disabled}
          aria-label={`${isLoaded ? t('卸載', 'Unload') : t('載入', 'Load')} ${displayName}`}
          onClick={()=>onAction(node, modelId, isLoaded ? 'unload' : 'load', undefined, !isLoaded && shouldReplace)}
        >
          {operation ? <CircleNotch size={14} className="gcc-spin"/> : isLoaded ? <Eject size={14}/> : <DownloadSimple size={14}/>}
          {operation ? (operation==='load' ? t('載入中', 'Loading') : t('卸載中', 'Unloading')) : isLoaded ? t('卸載', 'Unload') : t('載入', 'Load')}
        </button>}
      </div>
    </li>;
  };

  return <section className="gcc-comfy" aria-label={t(`${node.name} ComfyUI 狀態`, `${node.name} ComfyUI status`)}>
    <div className="gcc-comfy-head">
      <FlowArrow size={23}/>
      <h4>ComfyUI</h4>
      <Badge value={node.status==='offline' || comfy?.status!=='online' ? 'offline' : 'online'}/>
      {comfy?.version && <span className="gcc-muted">v{comfy.version}</span>}
      {comfy?.status==='online' && <button type="button" className="gcc-button gcc-button--quiet gcc-comfy-free-btn" disabled={disabled} onClick={()=>onAction(node,'comfyui:*','unload')}>
        {isUnloadingAll ? <CircleNotch size={14} className="gcc-spin"/> : <Eject size={14}/>}{isUnloadingAll ? t('釋放中', 'Freeing') : t('釋放顯存', 'Free VRAM')}
      </button>}
    </div>
    {!comfy || (comfy.status!=='online' && !allGroups.length) ? <p className="gcc-muted">{comfy ? t('無法連接 ComfyUI，且未偵測到磁碟模型目錄；請確認服務或 COMFYUI_MODELS_DIR 設定。', 'Unable to reach ComfyUI and no disk model directory detected; check service or COMFYUI_MODELS_DIR.') : t('等待 Agent 讀取 ComfyUI。', 'Waiting for host agent to inspect ComfyUI.')}</p> : <>
      {comfy.status!=='online' && <p className="gcc-muted">{t(`ComfyUI 服務未啟動，以下清單直接讀取自磁碟目錄${comfy.models_dir ? `（${comfy.models_dir}）` : ''}；啟動服務後可預載與釋放快取。`, `ComfyUI service is offline; listing models directly from disk${comfy.models_dir ? ` (${comfy.models_dir})` : ''}. Start ComfyUI to preload or free cache.`)}</p>}
      {node.status==='offline' && <p className="gcc-muted">{t('主機離線，以下為最後回報資料。', 'Host offline — showing last reported snapshot.')}</p>}
      {comfy.status==='online' && <>
        <div className="gcc-comfy-counts"><span><Queue size={18}/>{t('執行中', 'Running')} <strong>{comfy.queue_status==='online' ? comfy.running?.length ?? 0 : '—'}</strong></span><span>{t('等待中', 'Pending')} <strong>{comfy.queue_status==='online' ? comfy.pending?.length ?? 0 : '—'}</strong></span></div>
        {activeModels.length > 0 && <div className="gcc-comfy-active" aria-label={activeModelsTitle}>
          <div className="gcc-comfy-section-head">
            <span className="gcc-comfy-section-label"><Stack size={15}/>{activeModelsTitle}</span>
            <span className="gcc-badge">{activeModels.length}</span>
          </div>
          <ul className="gcc-comfy-active-list">
            {activeModels.map((m, i) => {
              const isPrimary = m.role === 'diffusion' || m.role === 'checkpoint';
              const display = modelDisplayName(node, `comfyui:${m.name}`, m.name);
              const hasVram = m.vram_bytes != null;
              const vramBytes = m.vram_bytes ?? 0;
              const offloadedBytes = m.offloaded_bytes ?? 0;
              const showOffload = hasVram && offloadedBytes >= 64 * 1024 ** 2;
              return <li key={`${m.name}-${i}`}>
                <div className="gcc-gpu-proc-model-left">
                  <span className={`gcc-comfy-role${isPrimary ? ' is-primary' : ''}`}>{comfyRoleLabel(m.role)}</span>
                  <span className="gcc-comfy-active-name" title={m.name}>{display}</span>
                </div>
                {hasVram && <span className="gcc-gpu-proc-mem-group">
                  {m.gpu_index != null && gpus.length > 1 && <code className="gcc-gpu-proc-mem is-device">{shortGpuLabel(m.gpu_index)}</code>}
                  {vramBytes > 0 ? <>
                    <code className="gcc-gpu-proc-mem is-vram">{formatBytes(vramBytes)}</code>
                    {showOffload && <code className="gcc-gpu-proc-mem is-offload">+{formatBytes(offloadedBytes)} RAM</code>}
                  </> : (
                    <code className="gcc-gpu-proc-mem is-offload">RAM {formatBytes(offloadedBytes || m.total_bytes)}</code>
                  )}
                </span>}
              </li>;
            })}
          </ul>
        </div>}
        {comfy.queue_status!=='online' ? <p className="gcc-muted">{t('暫時無法取得佇列。', 'Queue temporarily unavailable.')}</p> : ((comfy.running?.length || 0)+(comfy.pending?.length || 0)>0) && <details className="gcc-disclosure"><summary>{t('查看服務佇列', 'View Service Queue')}</summary><div><ul className="gcc-comfy-queue">{comfy.running?.map(j=><li key={j.id}>{t('執行中', 'Running')} <code>{j.id}</code></li>)}{comfy.pending?.map(j=><li key={j.id}>{t('等待中', 'Pending')} <code>{j.id}</code></li>)}</ul></div></details>}
      </>}

      {statusBanner && <div role="status" className={`gcc-model-op-banner gcc-model-op-banner--${statusBanner.tone}`}>
        {statusBanner.tone==='info' ? <CircleNotch size={15} className="gcc-spin"/> : <WarningCircle size={15}/>}
        <span>{statusBanner.text}</span>
        {statusBanner.dismissible && <button type="button" className="gcc-icon-button gcc-banner-close" aria-label={t('關閉訊息', 'Dismiss message')} onClick={()=>setDismissedErrorId(statusBanner.id)}><X size={14}/></button>}
      </div>}

      {comfy.models_status==='unavailable' ? <p className="gcc-muted">{t('暫時無法取得模型清單。', 'Model catalog temporarily unavailable.')}</p> : <div className="gcc-comfy-featured">
        <div className="gcc-comfy-section-head">
          <span className="gcc-comfy-section-label"><Stack size={15}/>{effectiveShowHidden ? t('已隱藏模型', 'Hidden Models') : 'Diffusion Models'}</span>
          <div className="gcc-comfy-section-tools">
            {hiddenLoadableFiles.length > 0 && <button type="button" className={`gcc-filter-pill${effectiveShowHidden ? ' is-active' : ''}`} onClick={()=>{ setShowHidden(v=>!v); setCheckedIds([]); }}>
              <EyeSlash size={12}/>{t('已隱藏', 'Hidden')}<span>{hiddenLoadableFiles.length}</span>
            </button>}
            {(visibleDiffusionFiles.length > 0 || effectiveShowHidden) && <button type="button" className={`gcc-filter-pill${batchMode ? ' is-active' : ''}`} onClick={()=>{ setBatchMode(v=>!v); setCheckedIds([]); }}>
              <CheckSquare size={12}/>{batchMode ? t('完成', 'Done') : t('批次', 'Batch')}
            </button>}
            <span className="gcc-badge">{effectiveShowHidden ? hiddenLoadableFiles.length : (diffusionGroup?.status==='online' ? visibleDiffusionFiles.length : '—')}</span>
          </div>
        </div>
        {batchMode && <div className="gcc-batch-bar gcc-batch-bar--compact" role="region" aria-label={t('ComfyUI 批次隱藏操作列', 'ComfyUI batch hide bar')}>
          <button type="button" className="gcc-button gcc-btn-sm" onClick={toggleAll} disabled={!batchScopeIds.length}>{allChecked ? t('取消全選', 'Deselect All') : t('全選', 'Select All')}</button>
          <span className="gcc-batch-count">{t('已選', 'Selected')} <strong>{activeChecked.length}</strong></span>
          <div className="gcc-batch-actions">
            <button type="button" className={`gcc-button gcc-btn-sm${effectiveShowHidden ? ' gcc-button--primary' : ' gcc-button--unload'}`} disabled={busy || !activeChecked.length} onClick={applyBatch}>
              {effectiveShowHidden ? <Eye size={13}/> : <EyeSlash size={13}/>}
              {effectiveShowHidden ? t(`恢復 (${activeChecked.length})`, `Unhide (${activeChecked.length})`) : t(`隱藏 (${activeChecked.length})`, `Hide (${activeChecked.length})`)}
            </button>
          </div>
        </div>}
        {effectiveShowHidden ? (
          <ul className="gcc-comfy-load-list">{hiddenLoadableFiles.map(item => renderLoadableRow(item.file, true, true))}</ul>
        ) : diffusionGroup && diffusionGroup.status==='online' && visibleDiffusionFiles.length ? (
          <ul className="gcc-comfy-load-list">{visibleDiffusionFiles.map(f => renderLoadableRow(f, true, false))}</ul>
        ) : (
          <p className="gcc-muted">{diffusionGroup?.status==='unavailable' ? t('無法讀取 diffusion_models。', 'Unable to read diffusion_models.') : hiddenLoadableFiles.length ? t('Diffusion Models 已全部隱藏，可點上方「已隱藏」恢復。', 'All diffusion models are hidden. Click "Hidden" above to restore.') : t('目前沒有 diffusion_models 檔案。', 'No files in diffusion_models.')}</p>
        )}
        {!effectiveShowHidden && diffusionGroup?.truncated && <p className="gcc-muted">{t('僅顯示前 200 筆。', 'Showing first 200 entries.')}</p>}
      </div>}

      {comfy.models_status!=='unavailable' && (populatedOthers.length>0 || auxGroups.length>0) && <details className="gcc-disclosure"><summary><Stack size={18}/>{t('其他模型分類', 'Other Model Categories')}<span>{t(`${otherModelsCount} 個模型 · ${populatedOthers.length} 個分類`, `${otherModelsCount} models · ${populatedOthers.length} folders`)}</span></summary><div>
        {comfy.models_status==='partial' && <p className="gcc-muted">{t('部分分類無法讀取或超出顯示上限。', 'Some categories could not be read or exceeded the display cap.')}</p>}
        {populatedOthers.map(group=>{
          const isLoadable = LOADABLE_FOLDERS.has(group.folder);
          const files = isLoadable ? (group.files || []).filter(f => !hiddenSet.has(`comfyui:${f}`)) : (group.files || []);
          return <details className="gcc-disclosure" key={group.folder}><summary>{group.folder}<span>{group.status==='online' ? (isLoadable ? files.length : group.count) : t('無法讀取', 'Unavailable')}</span></summary><div>{files.length ? (isLoadable ? <ul className="gcc-comfy-load-list">{files.map(f => renderLoadableRow(f, false, false))}</ul> : <ul className="gcc-comfy-models">{files.map(file=><li key={file}>{file}</li>)}</ul>) : <p className="gcc-muted">{t('此分類沒有顯示中的模型。', 'No visible models in this category.')}</p>}{group.truncated && <p className="gcc-muted">{t('僅顯示前 200 筆。', 'Showing first 200 entries.')}</p>}</div></details>;
        })}
        {emptyModelFolders>0 && <p className="gcc-loaded-note">{t(`已隱藏 ${emptyModelFolders} 個沒有檔案的空模型分類。`, `${emptyModelFolders} empty model folder(s) hidden.`)}</p>}
        {!!auxGroups.length && <details className="gcc-disclosure"><summary>{t('其他 ComfyUI 目錄（非模型）', 'Other ComfyUI Folders (Non-model)')}<span>{auxGroups.map(g=>`${g.folder} ${g.count}`).join(' · ')}</span></summary><div>{auxGroups.map(group=><details className="gcc-disclosure" key={group.folder}><summary>{group.folder}<span>{group.count}</span></summary><div><ul className="gcc-comfy-models">{group.files.map(file=><li key={file}>{file}</li>)}</ul>{group.truncated && <p className="gcc-muted">{t('僅顯示前 200 筆。', 'Showing first 200 entries.')}</p>}</div></details>)}</div></details>}
      </div></details>}
      {!!comfy.devices?.length && <div className="gcc-comfy-devices"><span className="gcc-comfy-section-label"><Cpu size={15}/>{t('ComfyUI 裝置顯存（服務回報可用 / 總容量）', 'ComfyUI Device VRAM (Free / Total)')}</span><ul>{comfy.devices.map((device,i)=><li key={i}><strong>{formatDeviceName(device.name)}</strong><code>{(device.vram_free/1024**3).toFixed(1)} / {(device.vram_total/1024**3).toFixed(1)} GiB {t('可用', 'free')}</code></li>)}</ul></div>}
      <p className="gcc-loaded-note">{node.maintenance ? t('主機已停止接單，恢復後才能操作。', 'Host is in maintenance mode; resume scheduling to operate.') : !node.capabilities.includes('comfyui.model_control') ? t('請更新並重啟主機 Agent 以啟用 ComfyUI 模型載入與卸載。', 'Update and restart the host agent to enable ComfyUI model load/unload.') : t('點選模型列上的 GPU 按鈕可直接預載至 ComfyUI 快取並指定裝置；批次隱藏僅從列表移除顯示，不會刪除硬碟檔案。', 'Click a GPU button on any model row to preload onto that ComfyUI device; batch hide only removes items from this view.')}</p>
    </>}
  </section>;
}
