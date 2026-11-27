import { useMemo, useState } from 'react';
import { ArrowRight, ArrowsLeftRight, CaretDown, CircleNotch, Cpu, Cube, Database, DesktopTower, DotsSixVertical, DownloadSimple, Eject, Lightning, Pause, Play, Stack, Thermometer, Queue, CheckCircle, WarningCircle, X } from '@phosphor-icons/react';
import { formatBytes, modelDisplayName, type GPU, type Job, type LoadedModel, type Node } from '../api';
import { useI18n } from '../i18n';
import { Badge, Empty } from './ui';
import {type ModelActionHandler, type ModelHiddenHandler, type ModelRenameHandler} from './ModelControls';
import {ComfyPanel} from './ComfyPanel';
import {HostAuthControl} from './HostAuthDialogs';
import {
  buildComfyDragPayload,
  buildLmStudioDragPayload,
  evaluateGpuDrop,
  formatDragMb,
  gpuCode,
  useModelDrag,
} from './modelDrag';

const valid = (value?: number | null): value is number => value != null && Number.isFinite(value);
const percent = (value?: number | null) => valid(value) ? `${Math.round(value)}%` : '—';
const memory = (value?: number | null) => valid(value) ? (value / 1024).toFixed(1) : '—';
const formatMb = (value?: number | null) => !valid(value) ? '—' : value >= 1024 ? `${(value / 1024).toFixed(2)} GiB` : `${Math.round(value)} MiB`;

interface GpuResidentItem {
  id: string;
  rawName: string;
  provider: 'lmstudio' | 'ollama';
  name: string;
  memory_used_mb: number | null;
  role: 'pinned' | 'auto' | 'context' | 'observed';
  loadedModel: LoadedModel;
}

function residentsOnGpu(node: Node, gpuIndex: number): GpuResidentItem[] {
  const items: GpuResidentItem[] = [];
  for (const provider of ['lmstudio', 'ollama'] as const) {
    const models = node.metrics[provider]?.loaded_models || [];
    for (const model of models) {
      const obs = model.observed_gpus?.find(g => g.index === gpuIndex);
      if (!obs) continue;
      let role: GpuResidentItem['role'] = 'observed';
      const cfg = model.configured_placement;
      if (provider === 'lmstudio' && cfg) {
        if (cfg.mode === 'pinned') role = cfg.gpu_indices.includes(gpuIndex) ? 'pinned' : 'context';
        else if (cfg.mode === 'auto') role = 'auto';
      }
      const id = `${provider}:${model.name}`;
      items.push({ id, rawName: model.name, provider, name: modelDisplayName(node, id, model.name), memory_used_mb: obs.memory_used_mb, role, loadedModel: model });
    }
  }
  items.sort((a, b) => (b.memory_used_mb ?? -1) - (a.memory_used_mb ?? -1));
  return items;
}

function LoadRing({value,label,compact=false}:{value:number|null|undefined;label:string;compact?:boolean}) {
  const { t } = useI18n();
  const amount=valid(value) ? Math.max(0,Math.min(100,value)) : 0;
  const toneClass = !valid(value) ? 'is-idle' : amount >= 85 ? 'is-high' : amount > 5 ? 'is-active' : 'is-idle';
  return <div className={`gcc-load-ring ${toneClass}${compact ? ' is-compact' : ''}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={valid(value) ? Math.round(amount) : undefined} aria-valuetext={valid(value) ? `${Math.round(amount)}%` : t('無資料', 'No data')}>
    <svg viewBox="0 0 100 100" aria-hidden="true"><circle className="gcc-ring-track" cx="50" cy="50" r="42"/><circle className="gcc-ring-fill" cx="50" cy="50" r="42" pathLength="100" strokeDasharray={`${amount} 100`} transform="rotate(-90 50 50)"/></svg>
    <div><strong>{valid(value) ? Math.round(value) : '—'}<small>{valid(value) ? '%' : ''}</small></strong><span>{label}</span></div>
  </div>;
}

function GraphicsCard() {
  const { t } = useI18n();
  return <svg className="gcc-card-illustration" viewBox="0 0 320 124" role="img" aria-label={t('顯示卡外形示意圖，非實際風扇轉速或硬體照片', 'Stylized GPU illustration (not live fan RPM or hardware photo)')}>
    <path className="gcc-board-bracket" d="M16 22h9v86h-9M16 34H8v18h8M16 65H8v18h8"/>
    <rect className="gcc-board-shell" x="25" y="20" width="280" height="82" rx="10"/>
    <path className="gcc-board-top" d="M36 20h256l-13-8H49z"/>
    {[78,165,252].map(cx => <g key={cx}><circle className="gcc-fan-rim" cx={cx} cy="61" r="32"/>{Array.from({length:7},(_,i) => <path className="gcc-fan-blade" key={i} d={`M${cx} 53c-17-17-26-6-25 4 8-8 15-2 19 5z`} transform={`rotate(${i*360/7} ${cx} 61)`}/>)}<circle className="gcc-fan-center" cx={cx} cy="61" r="7"/></g>)}
    <path className="gcc-board-connector" d="M89 102v9h38v-9m8 0v9h96v-9"/>
    {Array.from({length:16},(_,i) => <path className="gcc-connector-pin" d={`M${94+i*8} 104v5`} key={i}/>)}
    <path className="gcc-board-edge" d="M38 92h253M37 30h254"/>
    <rect className="gcc-board-indicator" x="285" y="56" width="8" height="10" rx="2"/>
  </svg>;
}

function GpuDevice({node,gpu,index,simulated,offline,residents,jobs,busy,onModelAction}:{node:Node;gpu:GPU;index:number;simulated:boolean;offline:boolean;residents:GpuResidentItem[];jobs:Job[];busy:boolean;onModelAction:ModelActionHandler}) {
  const { locale, t, comfyRoleLabel, processCategoryLabel, translateApiText } = useI18n();
  const [selectedModelId, setSelectedModelId] = useState('');
  const { dragItem, dropTarget, startDrag, endDrag, setDropTarget, handleDropOnGpu } = useModelDrag();
  const gpuIndex = gpu.index ?? index;
  const hostGpuCount = (node.metrics.gpus || []).length;
  const ratio=valid(gpu.memory_used_mb) && valid(gpu.memory_total_mb) && gpu.memory_total_mb>0 ? gpu.memory_used_mb/gpu.memory_total_mb*100 : null;
  const highMemory=ratio!==null && ratio>90;

  const trackedMb = residents.reduce((sum, r) => sum + (valid(r.memory_used_mb) ? r.memory_used_mb : 0), 0);
  const otherProcesses = useMemo(() => (simulated ? [] : (gpu.other_processes || [])), [gpu.other_processes, simulated]);
  const otherComputeMb = otherProcesses.reduce((sum, p) => sum + (valid(p.memory_used_mb) ? p.memory_used_mb : 0), 0);
  const systemRemainderMb = !simulated && valid(gpu.memory_used_mb) ? Math.max(0, gpu.memory_used_mb - trackedMb - otherComputeMb) : 0;
  const showSystemRow = !simulated && valid(gpu.memory_used_mb) && systemRemainderMb >= 64;
  const totalOtherMb = otherComputeMb + (showSystemRow ? systemRemainderMb : 0);

  const activeJob = jobs.find(j => (j.node_id === node.id || j.parameters.target_node_id === node.id) && (['running', 'cancelling', 'unknown'].includes(j.status) || (j.status === 'queued' && !!j.parameters.model_action)));
  const isProviderControlDisabled = (provider: 'lmstudio' | 'ollama' | 'comfyui') => {
    const latest = jobs.find(j => j.model.startsWith(`${provider}:`) && j.parameters.model_action && j.parameters.target_node_id === node.id);
    const syncing = latest?.status === 'completed' && !!latest.finished_at && latest.finished_at > node.last_seen;
    const runtimeOnline = provider === 'comfyui'
      ? node.metrics.comfyui?.status === 'online'
      : node.metrics[provider]?.loaded_models_status === 'online';
    return busy || !!activeJob || syncing || node.status !== 'online' || node.maintenance || !runtimeOnline || !node.capabilities.includes(`${provider}.model_control`);
  };

  const comfyLoadableSet = useMemo(
    () => new Set((node.model_catalog || []).filter(m => m.runtime === 'comfyui').map(m => m.name)),
    [node.model_catalog],
  );
  const canDragLms = !simulated && hostGpuCount > 1 && node.metrics.lmstudio?.gpu_placement_supported !== false && !isProviderControlDisabled('lmstudio');
  const canDragComfy = !simulated && hostGpuCount > 1 && node.metrics.comfyui?.gpu_placement_supported !== false && !isProviderControlDisabled('comfyui');
  const dropEval = !simulated && dragItem ? evaluateGpuDrop(dragItem, node, gpu, gpuIndex, jobs, busy, locale) : null;
  const isDropOver = dropTarget?.nodeId === node.id && dropTarget.gpuIndex === gpuIndex;
  const isDropReady = Boolean(!simulated && dragItem && dropEval?.allowed);
  const isIncoming = Boolean(
    !simulated &&
    activeJob &&
    activeJob.parameters.model_action === 'load' &&
    activeJob.parameters.gpu_indices?.includes(gpuIndex),
  );
  const incomingLabel = isIncoming && activeJob
    ? modelDisplayName(node, activeJob.model, activeJob.model.replace(/^(lmstudio|ollama|comfyui):/, ''))
    : null;
  const hasDraggableResident = canDragLms && residents.some(r => r.provider === 'lmstudio');

  const loadableModels = useMemo(() => {
    if (simulated) return [];
    const hidden = new Set(node.hidden_models || []);
    const primary = node.default_runtime || 'lmstudio';
    const order = primary === 'lmstudio' ? (['lmstudio', 'ollama'] as const) : (['ollama', 'lmstudio'] as const);
    const items: { id: string; name: string; displayName: string; provider: 'lmstudio' | 'ollama' }[] = [];
    for (const provider of order) {
      if (node.metrics[provider]?.loaded_models_status !== 'online') continue;
      if (!node.capabilities.includes(`${provider}.model_control`)) continue;
      for (const m of (node.model_catalog || [])) {
        if (m.runtime !== provider || hidden.has(m.id)) continue;
        items.push({ id: m.id, name: m.name, displayName: modelDisplayName(node, m.id, m.name), provider });
      }
    }
    return items;
  }, [node, simulated]);

  const lmsLoadable = loadableModels.filter(m => m.provider === 'lmstudio');
  const ollamaLoadable = loadableModels.filter(m => m.provider === 'ollama');
  const residentIdSet = useMemo(() => {
    const set = new Set<string>();
    for (const provider of ['lmstudio', 'ollama'] as const) {
      for (const m of (node.metrics[provider]?.loaded_models || [])) set.add(`${provider}:${m.name}`);
    }
    return set;
  }, [node.metrics]);

  const selectedModel = loadableModels.find(m => m.id === selectedModelId)
    || loadableModels.find(m => !residentIdSet.has(m.id))
    || loadableModels[0];

  const selectedResident = selectedModel
    ? (node.metrics[selectedModel.provider]?.loaded_models || []).find(m => m.name === selectedModel.name)
    : undefined;
  const canPinSelected = selectedModel?.provider === 'lmstudio' && node.metrics.lmstudio?.gpu_placement_supported !== false;
  const isAlreadyPinnedHere = Boolean(
    canPinSelected &&
    selectedResident?.configured_placement?.mode === 'pinned' &&
    selectedResident.configured_placement.gpu_indices.length === 1 &&
    selectedResident.configured_placement.gpu_indices[0] === gpuIndex,
  );
  const isAlreadyLoadedNonPin = Boolean(selectedModel && !canPinSelected && selectedResident);
  const isLoadingSelected = Boolean(selectedModel && activeJob?.model === selectedModel.id && activeJob.parameters.model_action === 'load');
  const isReassignSelected = Boolean(canPinSelected && selectedResident && !isAlreadyPinnedHere);
  const loadDisabled = !selectedModel || isProviderControlDisabled(selectedModel.provider) || isAlreadyPinnedHere || isAlreadyLoadedNonPin;
  const loadButtonText = isLoadingSelected
    ? t('載入中', 'Loading…')
    : isAlreadyPinnedHere
      ? t('已在此卡', 'On GPU')
      : isAlreadyLoadedNonPin
        ? t('已載入', 'Loaded')
        : isReassignSelected
          ? t('改指定此卡', 'Move Here')
          : canPinSelected
            ? t('載入此卡', 'Load Here')
            : t('載入模型', 'Load');
  const selectedObservedGpuText = selectedResident?.observed_gpus?.length
    ? selectedResident.observed_gpus.map(g => gpuCode(g.index)).join(', ')
    : null;
  const quickloadHint = isAlreadyPinnedHere
    ? t(`已固定於 ${gpuCode(gpuIndex)}`, `Pinned to ${gpuCode(gpuIndex)}`)
    : isAlreadyLoadedNonPin
      ? t('已於主機常駐', 'Loaded on host')
      : isReassignSelected
        ? selectedObservedGpuText
          ? t(`目前於 ${selectedObservedGpuText} · 將移至此卡`, `On ${selectedObservedGpuText} · will move here`)
          : t('已常駐 · 將改指定此卡', 'Loaded · will reassign here')
        : canPinSelected
          ? t(`指定載入至 ${gpuCode(gpuIndex)}`, `Pin & load to ${gpuCode(gpuIndex)}`)
          : t('由執行器自動分配', 'Auto-placed by runtime');

  const deviceStateClass = offline || !valid(gpu.utilization)
    ? 'is-idle'
    : gpu.utilization >= 90
      ? 'is-high'
      : gpu.utilization > 5
        ? 'is-active'
        : 'is-idle';
  const deviceStateText = offline
    ? t('最後數據', 'Last Snapshot')
    : !valid(gpu.utilization)
      ? t('無數據', 'No Telemetry')
      : gpu.utilization >= 90
        ? t('高負載', 'High Load')
        : gpu.utilization > 5
          ? t('使用中', 'Active')
          : t('低負載', 'Idle');

  const tempClass = !valid(gpu.temperature_c)
    ? ''
    : gpu.temperature_c >= 75
      ? ' is-hot'
      : gpu.temperature_c >= 55
        ? ' is-warm'
        : ' is-cool';

  const deviceDropClass = [
    'gcc-device',
    highMemory ? 'is-memory-high' : '',
    offline ? 'is-stale' : '',
    isDropReady ? 'is-drop-ready' : '',
    isDropOver && dropEval?.allowed ? (dropEval.insufficientVram ? 'is-drop-warning' : 'is-drop-over') : '',
    isDropOver && dropEval && !dropEval.allowed ? 'is-drop-blocked' : '',
  ].filter(Boolean).join(' ');

  return <section
    className={deviceDropClass}
    aria-label={`GPU ${gpuIndex} ${gpu.name}`}
    onDragOver={e => {
      if (simulated || !dragItem) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = dropEval?.allowed ? 'move' : 'none';
      if (!isDropOver) setDropTarget({ nodeId: node.id, gpuIndex });
    }}
    onDragLeave={e => {
      if (!e.currentTarget.contains(e.relatedTarget as globalThis.Node | null)) {
        if (isDropOver) setDropTarget(null);
      }
    }}
    onDrop={e => {
      if (simulated) return;
      handleDropOnGpu(e, node, gpu, gpuIndex);
    }}
  >
    <div className="gcc-device-head"><div><span className="gcc-device-label">GPU {String(gpuIndex).padStart(2,'0')}</span><h4>{gpu.name.replace('NVIDIA GeForce ','')}</h4></div><span className={`gcc-device-state ${deviceStateClass}`}>{deviceStateText}</span></div>
    <GraphicsCard/>
    <div className="gcc-device-readings">
      <LoadRing value={gpu.utilization} label={t('運算負載', 'GPU Load')}/>
      <div className="gcc-memory-readout">
        <span><Database size={15}/>{t('顯存容量', 'VRAM Usage')}{ratio !== null && <b className="gcc-memory-pct">{Math.round(ratio)}%</b>}</span>
        <strong>{memory(gpu.memory_used_mb)}<small> / {memory(gpu.memory_total_mb)} GB</small></strong>
        <div
          className="gcc-memory-cells"
          role="meter"
          aria-label={t(`GPU ${gpuIndex} 顯存使用率`, `GPU ${gpuIndex} VRAM utilization`)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={ratio===null ? undefined : Math.round(ratio)}
          aria-valuetext={ratio===null ? t('等待顯存資料', 'Waiting for VRAM telemetry') : t(`已使用 ${Math.round(ratio)}%`, `${Math.round(ratio)}% used`)}
        >{Array.from({length:12},(_,i) => <span key={i}><i style={{transform:`scaleY(${ratio===null ? 0 : Math.max(0,Math.min(1,ratio/100*12-i))})`}}/></span>)}</div>
      </div>
    </div>
    <div className="gcc-device-vitals"><div className={`gcc-vital--temp${tempClass}`}><Thermometer size={20}/><span>{t('溫度', 'Temp')}<strong>{gpu.temperature_c ?? '—'}<small>°C</small></strong></span></div><div className="gcc-vital--power"><Lightning size={20}/><span>{t('功耗', 'Power')}<strong>{valid(gpu.power_w) ? Math.round(gpu.power_w) : '—'}<small>W</small></strong></span></div></div>
    {!simulated && <div className="gcc-gpu-models">
      <div className="gcc-gpu-section-head">
        <span className="gcc-gpu-models-label">
          <Stack size={14}/>
          <span>{t('此卡模型', 'Resident Models')}</span>
          <span className="gcc-gpu-section-tag">{t('實際觀測', 'Observed')}</span>
          {hasDraggableResident && !dragItem && <em className="gcc-drag-inline-hint">{t('可拖曳換卡', 'Drag to move')}</em>}
        </span>
        {trackedMb > 0 ? (
          <code className="gcc-gpu-section-total">{formatMb(trackedMb)}</code>
        ) : (
          <span className="gcc-gpu-idle-badge"><i aria-hidden="true"/>{t('待命', 'Standby')}</span>
        )}
      </div>
      {dragItem && isDropOver && dropEval && (
        <div className={`gcc-drop-banner${!dropEval.allowed ? ' is-blocked' : dropEval.insufficientVram ? ' is-warning' : ' is-ready'}`}>
          {!dropEval.allowed ? (
            <span>{dropEval.reason}</span>
          ) : dropEval.insufficientVram ? (
            <>
              <WarningCircle size={14}/>
              <span>{t(`顯存可能不足（模型約 ${formatDragMb(dropEval.estimatedVramMb)} / 此卡可用 ${formatDragMb(dropEval.effectiveFreeMb)}），放開後確認`, `Low VRAM (model ~${formatDragMb(dropEval.estimatedVramMb)} / free ${formatDragMb(dropEval.effectiveFreeMb)}). Drop to confirm.`)}</span>
            </>
          ) : (
            <>
              <ArrowsLeftRight size={14}/>
              <span>{t(`放開以${dragItem.isLoaded ? '搬移' : '載入'}「${dragItem.displayName}」至 ${gpuCode(gpuIndex)}${dropEval.effectiveFreeMb != null ? `（可用 ${formatDragMb(dropEval.effectiveFreeMb)}）` : ''}`, `Drop to ${dragItem.isLoaded ? 'move' : 'load'} "${dragItem.displayName}" onto ${gpuCode(gpuIndex)}${dropEval.effectiveFreeMb != null ? ` (${formatDragMb(dropEval.effectiveFreeMb)} free)` : ''}`)}</span>
            </>
          )}
        </div>
      )}
      {dragItem && !isDropOver && isDropReady && dropEval && (
        <div className={`gcc-drop-cue${dropEval.insufficientVram ? ' is-warning' : ''}`}>
          <ArrowsLeftRight size={13}/>
          <span>{t(`拖曳至此可移至 ${gpuCode(gpuIndex)}${dropEval.effectiveFreeMb != null ? ` · 可用 ${formatDragMb(dropEval.effectiveFreeMb)}` : ''}`, `Drop here to move to ${gpuCode(gpuIndex)}${dropEval.effectiveFreeMb != null ? ` · ${formatDragMb(dropEval.effectiveFreeMb)} free` : ''}`)}</span>
        </div>
      )}
      {(residents.length > 0 || (isIncoming && incomingLabel)) ? <ul>{residents.map((item,i)=>{
        const unloading = activeJob?.model === item.id && activeJob.parameters.model_action === 'unload';
        const unloadDisabled = isProviderControlDisabled(item.provider);
        const draggable = item.provider === 'lmstudio' && canDragLms;
        const isSelfDragging = dragItem?.nodeId === node.id && dragItem.modelId === item.id;
        const isMovingOut = Boolean(
          activeJob &&
          activeJob.model === item.id &&
          activeJob.parameters.model_action === 'load' &&
          !activeJob.parameters.gpu_indices?.includes(gpuIndex),
        );
        return <li
          key={`${item.id}-${i}`}
          draggable={draggable}
          className={`gcc-gpu-resident-row${item.role === 'pinned' ? ' is-pinned' : ''}${draggable ? ' is-draggable-row' : ''}${isSelfDragging ? ' is-dragging' : ''}${isMovingOut ? ' is-moving-out' : ''}`}
          title={
            item.provider === 'ollama'
              ? t('Ollama 目前不支援逐模型指定 GPU 搬移', 'Ollama does not currently support per-model GPU pinning')
              : draggable
                ? t(`拖曳 ${item.name} 至另一張 GPU 卡片以搬移`, `Drag ${item.name} to another GPU card to relocate`)
                : undefined
          }
          onDragStart={e => {
            if (!draggable) return;
            startDrag(e, buildLmStudioDragPayload(node, item.rawName, item.loadedModel, gpuIndex));
          }}
          onDragEnd={endDrag}
        >
          <div className="gcc-gpu-model-left">
            {draggable && <span className="gcc-drag-handle" aria-hidden="true"><DotsSixVertical size={14}/></span>}
            <span className="gcc-gpu-resident-dot" aria-hidden="true"/>
            <div>
              <strong>{item.name}</strong>
              <small>
                {item.provider==='lmstudio' ? (node.metrics.lmstudio?.backend==='direct_llama' ? t('GGUF 直載', 'Direct GGUF') : 'LM Studio') : 'Ollama'}
                {item.role==='pinned' ? t(' · 指定此卡', ' · Pinned') : item.role==='auto' ? t(' · 自動分配', ' · Auto') : item.role==='context' ? t(' · 附帶顯存', ' · Context VRAM') : ''}
                {item.provider==='ollama' && hostGpuCount > 1 ? t(' · 不支援拖移', ' · Drag unsupported') : ''}
                {isMovingOut ? t(' · 正在移出…', ' · Relocating…') : ''}
              </small>
            </div>
          </div>
          <div className="gcc-gpu-model-right">
            <code>{formatMb(item.memory_used_mb)}</code>
            <button
              type="button"
              className="gcc-button gcc-button--unload gcc-gpu-action-btn"
              disabled={unloadDisabled}
              aria-label={t(`卸載 ${item.name}`, `Unload ${item.name}`)}
              title={t(`從 ${item.provider==='lmstudio' ? 'LM Studio' : 'Ollama'} 卸載此模型`, `Unload this model from ${item.provider==='lmstudio' ? 'LM Studio' : 'Ollama'}`)}
              onClick={() => onModelAction(node, item.id, 'unload')}
            >
              {unloading ? <CircleNotch size={13} className="gcc-spin"/> : <Eject size={13}/>}
              {unloading ? t('卸載中', 'Unloading') : t('卸載', 'Unload')}
            </button>
          </div>
        </li>;
      })}
      {isIncoming && incomingLabel && <li className="gcc-gpu-incoming-row">
        <div className="gcc-gpu-model-left">
          <CircleNotch size={14} className="gcc-spin gcc-accent-text"/>
          <div>
            <strong>{incomingLabel}</strong>
            <small>{activeJob?.status === 'queued' ? t('等待 Agent 派送至此卡…', 'Waiting for agent dispatch to this GPU…') : t('正在載入至此卡…', 'Loading onto this GPU…')}</small>
          </div>
        </div>
      </li>}
      </ul> : <div className="gcc-gpu-models-empty">
        <span className="gcc-gpu-empty-icon" aria-hidden="true"><Cube size={15}/></span>
        <div className="gcc-gpu-empty-copy">
          <strong>{t('目前無已觀測常駐模型', 'No resident models observed')}</strong>
          <span>{loadableModels.length > 0 ? t('可由下方快速指派模型載入至此卡', 'Use quick assign below to load a model onto this GPU') : t('此卡目前無常駐推論模型', 'No active inference models on this card')}</span>
        </div>
      </div>}
      {loadableModels.length > 0 && <div className="gcc-gpu-quickload">
        <div className="gcc-gpu-quickload-head">
          <span className="gcc-gpu-quickload-title">{t('快速指派', 'Quick Assign')}</span>
          <span className={`gcc-gpu-quickload-hint${isReassignSelected ? ' is-move' : ''}`}>{quickloadHint}</span>
        </div>
        <div className="gcc-gpu-quickload-bar">
          <div className="gcc-gpu-select-wrap">
            <span className={`gcc-gpu-select-dot${selectedResident ? ' is-loaded' : ''}`} aria-hidden="true"/>
            <select
              aria-label={t(`選擇要載入到 GPU ${String(gpuIndex).padStart(2,'0')} 的模型`, `Select model to load onto GPU ${String(gpuIndex).padStart(2,'0')}`)}
              value={selectedModel?.id || ''}
              disabled={busy}
              onChange={e => setSelectedModelId(e.target.value)}
            >
              {lmsLoadable.length > 0 && ollamaLoadable.length > 0 ? <>
                <optgroup label={node.metrics.lmstudio?.backend==='direct_llama' ? t('GGUF 直載（可指定此卡）', 'Direct GGUF (Pin to GPU)') : t('LM Studio（可指定此卡）', 'LM Studio (Pin to GPU)')}>
                  {lmsLoadable.map(m => <option key={m.id} value={m.id}>{m.displayName}{residentIdSet.has(m.id) ? t(' · 已載入', ' · Loaded') : ''}</option>)}
                </optgroup>
                <optgroup label={t('Ollama（自動分配）', 'Ollama (Auto Placement)')}>
                  {ollamaLoadable.map(m => <option key={m.id} value={m.id}>{m.displayName}{residentIdSet.has(m.id) ? t(' · 已載入', ' · Loaded') : ''}</option>)}
                </optgroup>
              </> : loadableModels.map(m => <option key={m.id} value={m.id}>{m.displayName}{residentIdSet.has(m.id) ? t(' · 已載入', ' · Loaded') : ''}</option>)}
            </select>
            <CaretDown size={13} className="gcc-gpu-select-caret"/>
          </div>
          <button
            type="button"
            className={`gcc-button gcc-gpu-quickload-btn${isAlreadyPinnedHere || isAlreadyLoadedNonPin ? ' is-settled' : isReassignSelected ? ' is-reassign' : ''}`}
            disabled={loadDisabled}
            title={canPinSelected ? t(`指定載入到 GPU ${String(gpuIndex).padStart(2,'0')}`, `Pin and load onto GPU ${String(gpuIndex).padStart(2,'0')}`) : t('Ollama 不支援指定單卡，將由執行器自動分配', 'Ollama does not support single-GPU pinning; runtime places automatically')}
            onClick={() => { if (selectedModel) onModelAction(node, selectedModel.id, 'load', canPinSelected ? [gpuIndex] : null, !!selectedResident); }}
          >
            {isLoadingSelected
              ? <CircleNotch size={14} className="gcc-spin"/>
              : isAlreadyPinnedHere || isAlreadyLoadedNonPin
                ? <CheckCircle size={14}/>
                : isReassignSelected
                  ? <ArrowsLeftRight size={14}/>
                  : <DownloadSimple size={14}/>}
            <span>{loadButtonText}</span>
          </button>
        </div>
      </div>}
    </div>}
    {!simulated && (otherProcesses.length > 0 || showSystemRow) && <div className="gcc-gpu-other">
      <div className="gcc-gpu-section-head">
        <span className="gcc-gpu-models-label">
          <span>{t('其他程序顯存', 'Other GPU Processes')}</span>
          <span className="gcc-gpu-section-tag">{t('非本平台託管', 'Unmanaged')}</span>
        </span>
        <code className="gcc-gpu-section-total is-muted">{formatMb(totalOtherMb)}</code>
      </div>
      <ul>
        {otherProcesses.map((proc, i) => {
          const isComfy = proc.category === 'comfyui';
          const freeingComfy = isComfy && activeJob?.model === 'comfyui:*' && activeJob.parameters.model_action === 'unload';
          const canFreeComfy = isComfy && !isProviderControlDisabled('comfyui');
          const procModels = proc.models || [];
          const comfyRunning = isComfy && (node.metrics.comfyui?.running?.length || 0) > 0;
          return <li key={`${proc.pid ?? proc.label}-${i}`} className={procModels.length > 0 ? 'has-models' : undefined}>
            <div className="gcc-gpu-other-head">
              <div>
                <strong>{translateApiText(proc.label)}</strong>
                <small>
                  {processCategoryLabel(proc.category)}
                  {proc.pid != null ? ` · PID ${proc.pid}` : ''}
                  {isComfy && procModels.length > 0 ? (comfyRunning ? t(' · 執行中', ' · Running') : t(' · 快取中', ' · Cached')) : ''}
                </small>
              </div>
              <div className="gcc-gpu-model-right">
                <code>{formatMb(proc.memory_used_mb)}</code>
                {isComfy && node.capabilities.includes('comfyui.model_control') && <button
                  type="button"
                  className="gcc-button gcc-button--unload gcc-gpu-action-btn"
                  disabled={!canFreeComfy}
                  title={t('清空 ComfyUI 顯存快取', 'Free ComfyUI VRAM cache')}
                  onClick={() => onModelAction(node, 'comfyui:*', 'unload')}
                >
                  {freeingComfy ? <CircleNotch size={13} className="gcc-spin"/> : <Eject size={13}/>}
                  {freeingComfy ? t('釋放中', 'Freeing') : t('釋放快取', 'Free Cache')}
                </button>}
              </div>
            </div>
            {procModels.length > 0 && <div className="gcc-gpu-proc-models" aria-label={t(`${proc.label} 使用中的模型`, `Active models in ${proc.label}`)}>
              <div className="gcc-gpu-proc-models-head">
                <span>{t(`模型清單 · ${procModels.length}`, `Models · ${procModels.length}`)}</span>
                <span>{procModels.some(m => m.vram_bytes != null) ? t('ComfyUI 即時顯存', 'ComfyUI Live VRAM') : t('點擊檔名展開', 'Click filename to expand')}</span>
              </div>
              {procModels.map((m, mi) => {
                const isPrimary = m.role === 'diffusion' || m.role === 'checkpoint';
                const display = isComfy ? modelDisplayName(node, `comfyui:${m.name}`, m.name) : m.name;
                const canDragModel = isComfy && canDragComfy && comfyLoadableSet.has(m.name);
                const isSelfDragging = dragItem?.nodeId === node.id && dragItem.modelId === `comfyui:${m.name}`;
                const hasVramTelemetry = m.vram_bytes != null;
                const vramBytes = m.vram_bytes ?? 0;
                const offloadedBytes = m.offloaded_bytes ?? 0;
                const showOffload = hasVramTelemetry && offloadedBytes >= 64 * 1024 ** 2;
                const memTooltip = hasVramTelemetry
                  ? `VRAM ${formatBytes(vramBytes)}${m.total_bytes ? t(` / 總權重 ${formatBytes(m.total_bytes)}`, ` / Total ${formatBytes(m.total_bytes)}`) : ''}${showOffload ? ` · Offload RAM ${formatBytes(offloadedBytes)}` : ''}`
                  : undefined;
                return <div
                  key={`${m.name}-${mi}`}
                  draggable={canDragModel}
                  data-primary={isPrimary || undefined}
                  className={`gcc-gpu-proc-model${canDragModel ? ' is-draggable-row' : ''}${isSelfDragging ? ' is-dragging' : ''}`}
                  onDragStart={e => {
                    if (!canDragModel) return;
                    startDrag(e, buildComfyDragPayload(node, m.name, jobs, gpuIndex));
                  }}
                  onDragEnd={endDrag}
                >
                  <span className="gcc-gpu-proc-model-kind">
                    {canDragModel && <span className="gcc-drag-handle" title={t('拖曳至其他 GPU 卡片以切換 ComfyUI 裝置', 'Drag to another GPU card to switch ComfyUI device')} aria-hidden="true"><DotsSixVertical size={12}/></span>}
                    <span className={`gcc-comfy-role${isPrimary ? ' is-primary' : ''}`}>{comfyRoleLabel(m.role)}</span>
                    {hasVramTelemetry ? <span className="gcc-gpu-proc-mem-group" title={memTooltip}>
                      {vramBytes > 0 ? <>
                        <code className="gcc-gpu-proc-mem is-vram">{formatBytes(vramBytes)}</code>
                        {showOffload && <code className="gcc-gpu-proc-mem is-offload">+{formatBytes(offloadedBytes)} RAM</code>}
                      </> : (
                        <code className="gcc-gpu-proc-mem is-offload">RAM {formatBytes(offloadedBytes || m.total_bytes)}</code>
                      )}
                    </span> : <span className="gcc-gpu-proc-model-number" aria-hidden="true">{String(mi + 1).padStart(2, '0')}</span>}
                  </span>
                  <details className="gcc-gpu-proc-filename">
                    <summary aria-label={t(`完整模型名稱：${display}`, `Full model name: ${display}`)}>
                      <span className="gcc-gpu-proc-model-name">{display}</span><CaretDown size={14} aria-hidden="true"/>
                    </summary>
                    {(display !== m.name || hasVramTelemetry) && <div className="gcc-gpu-proc-fullname">
                      {display !== m.name && <div>{t(`原始檔名：${m.name}`, `Filename: ${m.name}`)}</div>}
                      {hasVramTelemetry && <small>VRAM {formatBytes(vramBytes)}{m.total_bytes ? t(` · 總權重 ${formatBytes(m.total_bytes)}`, ` · Total ${formatBytes(m.total_bytes)}`) : ''}{offloadedBytes > 0 ? ` · RAM ${formatBytes(offloadedBytes)}` : ''}</small>}
                    </div>}
                  </details>
                </div>;
              })}
            </div>}
          </li>;
        })}
        {showSystemRow && <li className="is-system-row">
          <div>
            <strong>{t('系統 / 桌面與圖形程序', 'System / Display & Graphics')}</strong>
            <small>{t('Xorg・瀏覽器・驅動常駐', 'Xorg · Browser · Driver overhead')}</small>
          </div>
          <div className="gcc-gpu-model-right">
            <code>{formatMb(systemRemainderMb)}</code>
          </div>
        </li>}
      </ul>
    </div>}
    {simulated && <span className="gcc-device-simulated">{t('模擬資料', 'Simulated Telemetry')}</span>}
  </section>;
}

function HostModelBanner({node, jobs}:{node:Node; jobs:Job[]}) {
  const { t, translateApiText } = useI18n();
  const [dismissedErrorId, setDismissedErrorId] = useState<string | null>(null);
  const latest = jobs.find(j => (j.model.startsWith('lmstudio:') || j.model.startsWith('ollama:')) && j.parameters.model_action && j.parameters.target_node_id === node.id);
  const active = jobs.find(j => (j.node_id === node.id || j.parameters.target_node_id === node.id) && (['running', 'cancelling', 'unknown'].includes(j.status) || (j.status === 'queued' && !!j.parameters.model_action)));
  const syncing = latest?.status === 'completed' && !!latest.finished_at && latest.finished_at > node.last_seen;

  const banner = active && (active.parameters.model_action || active.status === 'unknown')
    ? {
        id: active.id,
        dismissible: false,
        tone: active.status === 'unknown' ? 'warning' : 'info',
        text: active.status === 'unknown'
          ? t('上次操作狀態待確認，已保留主機工作槽。', 'Previous operation state unconfirmed; host execution slot reserved.')
          : t(`${active.name}：${active.status === 'queued' ? '等待 Agent 派送' : '正在處理，請稍候'}。`, `${active.name}: ${active.status === 'queued' ? 'waiting for agent dispatch' : 'in progress, please wait'}.`),
      }
    : syncing
      ? { id: 'syncing', dismissible: false, tone: 'info', text: t('正在同步模型最新狀態…', 'Syncing latest model residency…') }
      : latest?.status === 'failed' && dismissedErrorId !== latest.id
        ? { id: latest.id, dismissible: true, tone: 'error', text: t(`操作失敗：${latest.error || '請查看任務紀錄。'}`, `Operation failed: ${latest.error ? translateApiText(latest.error) : 'see job log.'}`) }
        : null;

  if (!banner) return null;
  return <div role="status" className={`gcc-model-op-banner gcc-model-op-banner--${banner.tone}`}>
    {banner.tone === 'info' ? <CircleNotch size={15} className="gcc-spin"/> : <WarningCircle size={15}/>}
    <span>{banner.text}</span>
    {banner.dismissible && <button type="button" className="gcc-icon-button gcc-banner-close" aria-label={t('關閉訊息', 'Dismiss message')} onClick={() => setDismissedErrorId(banner.id)}><X size={14}/></button>}
  </div>;
}

export function HardwarePanel({nodes,jobs,busy,onMaintenance,onModelAction,onRename,onHiddenChange,onManageModels,onUnlockHost,onLockHost,onManageHostPassword}:{nodes:Node[];jobs:Job[];busy:boolean;onMaintenance:(node:Node)=>void;onModelAction:ModelActionHandler;onRename:ModelRenameHandler;onHiddenChange:ModelHiddenHandler;onManageModels?:()=>void;onUnlockHost?:(node:Node)=>void;onLockHost?:(node:Node)=>void;onManageHostPassword?:(node:Node)=>void}) {
  const { t, formatTime } = useI18n();
  const gpuCount=nodes.reduce((sum,n)=>sum+(n.metrics.gpus?.length || 0),0);
  return <section className="gcc-panel gcc-hardware-panel" aria-labelledby="hardware-title"><div className="gcc-panel-head"><div><h2 id="hardware-title">{t('運算設備', 'Compute Hardware')}</h2><p>{t(`${nodes.length} 台主機 · ${gpuCount} 張顯示卡`, `${nodes.length} ${nodes.length === 1 ? 'host' : 'hosts'} · ${gpuCount} ${gpuCount === 1 ? 'GPU' : 'GPUs'}`)}</p></div><span className="gcc-subtle">{t('每 2 秒更新', '2s polling')}</span></div>
    {!nodes.length ? <Empty icon={<DesktopTower size={32}/>} title={t('等待設備連線', 'Waiting for Hardware')}>{t('啟動主機 Agent 後，硬體狀態會自動出現在這裡。', 'Start a host agent and live GPU telemetry will appear here automatically.')}</Empty> : nodes.map(node=><article className="gcc-host" key={node.id}>
      <div className="gcc-host-head">
        <div className="gcc-host-name"><span className="gcc-host-symbol"><DesktopTower size={20}/></span><div><h3>{node.name}</h3><p>{node.mode==='demo' ? t('模擬設備', 'Simulated Device') : node.connection_host ? t(`遠端服務 · ${node.connection_host}`, `Remote Service · ${node.connection_host}`) : t('本機運算主機', 'Local Compute Host')}</p></div></div>
        <div className="gcc-host-head-actions">
          {node.mode!=='demo' && onUnlockHost && onLockHost && onManageHostPassword && <HostAuthControl node={node} busy={busy} onUnlock={onUnlockHost} onLock={onLockHost} onManagePassword={onManageHostPassword}/>}
          {node.mode!=='demo' && onManageModels && <button type="button" className="gcc-button gcc-button--quiet gcc-btn-sm" onClick={onManageModels}><Stack size={15}/>{t('模型管理', 'Manage Models')}<ArrowRight size={13}/></button>}
          <Badge value={node.status}/>
        </div>
      </div>
      {node.mode!=='demo' && <HostModelBanner node={node} jobs={jobs}/>}
      <div className="gcc-device-grid">{node.metrics.gpus?.length ? node.metrics.gpus.map((gpu,i)=><GpuDevice node={node} gpu={gpu} index={i} simulated={node.mode==='demo'} offline={node.status==='offline'} residents={node.mode==='demo' ? [] : residentsOnGpu(node, gpu.index ?? i)} jobs={jobs} busy={busy} onModelAction={onModelAction} key={gpu.uuid || i}/>) : <p className="gcc-muted gcc-no-gpu">{node.connection_host ? t('硬體監測尚未連線；目前提供遠端模型服務。', 'Hardware telemetry unavailable; providing remote model services.') : t('尚未偵測到顯示卡。', 'No GPUs detected on this host.')}</p>}</div>
      <div className="gcc-system-resources"><div className="gcc-system-resource gcc-system-resource--cpu"><span className="gcc-chip-visual"><Cpu size={32}/><span>{Array.from({length:9},(_,i)=><i key={i} className={valid(node.metrics.cpu_percent) && i/9*100<node.metrics.cpu_percent ? 'is-filled' : ''}/>)}</span></span><div><span>{t('處理器 CPU', 'CPU Utilization')}</span><strong>{percent(node.metrics.cpu_percent)}<small>{t('使用中', 'in use')}</small></strong></div></div><div className="gcc-system-resource gcc-system-resource--ram"><span className="gcc-ram-visual" aria-hidden="true">{Array.from({length:5},(_,i)=><i key={i} className={valid(node.metrics.ram_percent) && i/5*100<node.metrics.ram_percent ? 'is-filled' : ''}/>)}</span><div><span>{t('系統記憶體', 'System RAM')}</span><strong>{percent(node.metrics.ram_percent)}<small>{node.metrics.ram_total_gb ? `/ ${node.metrics.ram_total_gb} GB` : t('使用中', 'in use')}</small></strong></div></div></div>
      {node.mode!=='demo' && <ComfyPanel node={node} jobs={jobs} busy={busy} onAction={onModelAction} onRename={onRename} onHiddenChange={onHiddenChange}/>}
      <div className="gcc-host-footer"><p className="gcc-heartbeat">{node.status==='offline' ? t('顯示最後收到的數據', 'Last received telemetry') : t('最後更新', 'Last updated')} <time dateTime={node.last_seen}>{formatTime(node.last_seen)}</time></p><button className="gcc-button gcc-button--quiet" disabled={busy} onClick={()=>onMaintenance(node)}>{node.maintenance ? <Play size={16}/> : <Pause size={16}/>}{node.maintenance ? t('恢復接單', 'Resume Scheduling') : t('停止接單', 'Pause Scheduling')}</button></div>
    </article>)}
  </section>;
}

export function SlotPanel({nodes,jobs}:{nodes:Node[];jobs:Job[]}) {
  const { t } = useI18n();
  const active=jobs.filter(j=>['running','cancelling','unknown'].includes(j.status));
  return <section className="gcc-panel gcc-slots" aria-labelledby="slots-title"><div className="gcc-panel-head"><div><h2 id="slots-title">{t('工作分配', 'Execution Slots')}</h2><p>{t('即時檢視各主機工作槽狀態', 'Live host slot allocation')}</p></div></div><div className="gcc-flow" aria-hidden="true"><Queue size={25}/><span/><Cpu size={29}/><span/><CheckCircle size={25}/></div>{nodes.length ? nodes.map(node=>{
    const holders=active.filter(j=>j.node_id===node.id);
    return <div className="gcc-slot" key={node.id}><div className="gcc-slot-label"><span>{node.name}</span><span className={holders.length ? 'gcc-accent-text' : 'gcc-muted'}>{holders.length ? t('工作中', 'Busy') : node.status==='online' ? t('待命中', 'Idle') : t('未接單', 'Unavailable')}</span></div>{holders.length ? holders.map(j=><div key={j.id} className="gcc-slot-job"><strong>{j.name}</strong><span>{j.source} · {j.model.replace('ollama:','')}</span><Badge value={j.status}/></div>) : <p className="gcc-slot-idle">{node.status==='online' ? t('目前沒有中央任務，可以開始新的推論。', 'No central job running. Ready for new inference workloads.') : t('等待主機恢復在線或接單。', 'Waiting for host to return online or exit maintenance.')}</p>}</div>;
  }) : <p className="gcc-slot-idle">{t('尚無已登錄主機。', 'No registered hosts yet.')}</p>}<div className="gcc-slot-note"><span className="gcc-keyline"/>{t('每台主機一次處理一個中央任務；硬體數據也包含其他程序的用量。', 'Each host runs one central job slot at a time; telemetry includes external processes.')}</div></section>;
}
