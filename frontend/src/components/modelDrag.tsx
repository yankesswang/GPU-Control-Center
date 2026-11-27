import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowsLeftRight, CircleNotch, Cpu, DotsSixVertical, DownloadSimple, WarningCircle, X } from '@phosphor-icons/react';
import { modelDisplayName, type GPU, type Job, type LoadedModel, type Node } from '../api';
import { useI18n, type Locale } from '../i18n';
import type { ModelActionHandler } from './ModelControls';

export const DRAG_MIME = 'application/x-gcc-model';

export interface DragModelPayload {
  nodeId: string;
  modelId: string;
  rawName: string;
  displayName: string;
  provider: 'lmstudio' | 'comfyui';
  isLoaded: boolean;
  replace: boolean;
  sourceGpuIndex: number | null;
  pinnedGpuIndices: number[] | null;
  estimatedVramMb: number | null;
  /** Per-GPU observed VRAM currently occupied by this model/runtime that would be freed on replace. */
  observedMbByGpu: Record<number, number>;
}

export interface DropTarget {
  nodeId: string;
  gpuIndex: number;
}

export interface DropEvaluation {
  allowed: boolean;
  sameGpu?: boolean;
  reason?: string;
  targetFreeMb: number | null;
  effectiveFreeMb: number | null;
  estimatedVramMb: number | null;
  insufficientVram: boolean;
  warningDetail?: string;
}

export interface PendingDropConfirm {
  node: Node;
  gpu: GPU;
  gpuIndex: number;
  payload: DragModelPayload;
  evaluation: DropEvaluation;
}

const LOW_FREE_VRAM_THRESHOLD_MB = 1024;

export const gpuCode = (idx: number) => `GPU ${String(idx).padStart(2, '0')}`;

export function formatDragMb(mb?: number | null): string {
  if (mb == null || !Number.isFinite(mb)) return '—';
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GiB` : `${Math.round(mb)} MiB`;
}

export function isInteractiveDragOrigin(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return Boolean(target.closest('button, input, textarea, select, option, a, label.gcc-batch-check'));
}

export function findHostActiveJob(node: Node, jobs: Job[]): Job | undefined {
  return jobs.find(
    j =>
      (j.node_id === node.id || j.parameters.target_node_id === node.id) &&
      (['running', 'cancelling', 'unknown'].includes(j.status) ||
        (j.status === 'queued' && !!j.parameters.model_action)),
  );
}

export function isProviderControlReady(
  node: Node,
  provider: 'lmstudio' | 'ollama' | 'comfyui',
  jobs: Job[],
  busy: boolean,
): boolean {
  if (busy || node.mode === 'demo' || node.status !== 'online' || node.maintenance) return false;
  if (!node.capabilities.includes(`${provider}.model_control`)) return false;
  if (findHostActiveJob(node, jobs)) return false;
  const latest = jobs.find(
    j =>
      j.model.startsWith(`${provider}:`) &&
      j.parameters.model_action &&
      j.parameters.target_node_id === node.id,
  );
  const syncing =
    latest?.status === 'completed' && !!latest.finished_at && latest.finished_at > node.last_seen;
  if (syncing) return false;
  return provider === 'comfyui'
    ? node.metrics.comfyui?.status === 'online'
    : node.metrics[provider]?.loaded_models_status === 'online';
}

export function buildLmStudioDragPayload(
  node: Node,
  rawName: string,
  resident: LoadedModel | undefined,
  sourceGpuIndex: number | null,
): DragModelPayload {
  const modelId = `lmstudio:${rawName}`;
  const displayName = modelDisplayName(node, modelId, rawName);
  const observedMbByGpu: Record<number, number> = {};
  let totalObserved = 0;
  let hasObserved = false;
  for (const g of resident?.observed_gpus || []) {
    if (typeof g.index === 'number' && g.memory_used_mb != null && Number.isFinite(g.memory_used_mb)) {
      observedMbByGpu[g.index] = (observedMbByGpu[g.index] || 0) + g.memory_used_mb;
      totalObserved += g.memory_used_mb;
      hasObserved = true;
    }
  }
  const estimatedVramMb = hasObserved && totalObserved > 0
    ? totalObserved
    : resident?.vram_bytes && resident.vram_bytes > 0
      ? resident.vram_bytes / (1024 * 1024)
      : null;

  const cfg = resident?.configured_placement;
  const pinnedGpuIndices =
    resident && cfg?.mode === 'pinned' && cfg.gpu_indices.length
      ? [...cfg.gpu_indices].sort((a, b) => a - b)
      : null;

  return {
    nodeId: node.id,
    modelId,
    rawName,
    displayName,
    provider: 'lmstudio',
    isLoaded: Boolean(resident),
    replace: Boolean(resident),
    sourceGpuIndex,
    pinnedGpuIndices,
    estimatedVramMb,
    observedMbByGpu,
  };
}

export function getComfyPreloadedInfo(node: Node, jobs: Job[]): {
  model: string | null;
  device: string | null;
  gpuIndex: number | null;
} {
  const lastCompleted = jobs.find(
    j =>
      j.model.startsWith('comfyui:') &&
      j.parameters.model_action &&
      j.parameters.target_node_id === node.id &&
      j.status === 'completed',
  );
  if (lastCompleted?.parameters.model_action !== 'load') {
    return { model: null, device: null, gpuIndex: null };
  }
  const model = lastCompleted.model.slice('comfyui:'.length);
  const device =
    typeof lastCompleted.result?.target_device === 'string'
      ? lastCompleted.result.target_device
      : null;
  const match = device?.match(/^gpu:(\d+)$/);
  return { model, device, gpuIndex: match ? Number(match[1]) : null };
}

export function buildComfyDragPayload(
  node: Node,
  rawName: string,
  jobs: Job[],
  sourceGpuIndex: number | null,
): DragModelPayload {
  const modelId = `comfyui:${rawName}`;
  const displayName = modelDisplayName(node, modelId, rawName);
  const preloaded = getComfyPreloadedInfo(node, jobs);
  const isThisPreloaded = preloaded.model === rawName;

  const observedMbByGpu: Record<number, number> = {};
  let totalComfyMb = 0;
  let hasComfyMb = false;
  for (const gpu of node.metrics.gpus || []) {
    if (typeof gpu.index !== 'number') continue;
    for (const proc of gpu.other_processes || []) {
      if (proc.category === 'comfyui' && proc.memory_used_mb != null && Number.isFinite(proc.memory_used_mb)) {
        observedMbByGpu[gpu.index] = (observedMbByGpu[gpu.index] || 0) + proc.memory_used_mb;
        totalComfyMb += proc.memory_used_mb;
        hasComfyMb = true;
      }
    }
  }

  const pinnedGpuIndices =
    isThisPreloaded && preloaded.gpuIndex !== null
      ? [preloaded.gpuIndex]
      : sourceGpuIndex !== null
        ? [sourceGpuIndex]
        : null;

  return {
    nodeId: node.id,
    modelId,
    rawName,
    displayName,
    provider: 'comfyui',
    isLoaded: isThisPreloaded || sourceGpuIndex !== null,
    replace: Boolean(preloaded.model) || sourceGpuIndex !== null,
    sourceGpuIndex,
    pinnedGpuIndices,
    estimatedVramMb: hasComfyMb && totalComfyMb > 0 ? totalComfyMb : null,
    observedMbByGpu,
  };
}

export function evaluateGpuDrop(
  payload: DragModelPayload,
  targetNode: Node,
  targetGpu: GPU,
  targetGpuIndex: number,
  jobs: Job[],
  busy: boolean,
  locale: Locale = 'zh-TW',
): DropEvaluation {
  const tr = (zh: string, en: string) => (locale === 'zh-TW' ? zh : en);
  const hasMem =
    targetGpu.memory_total_mb != null &&
    targetGpu.memory_used_mb != null &&
    Number.isFinite(targetGpu.memory_total_mb) &&
    Number.isFinite(targetGpu.memory_used_mb) &&
    targetGpu.memory_total_mb > 0;
  const targetFreeMb = hasMem
    ? Math.max(0, targetGpu.memory_total_mb! - targetGpu.memory_used_mb!)
    : null;
  const reclaimableMb = payload.replace ? payload.observedMbByGpu[targetGpuIndex] || 0 : 0;
  const effectiveFreeMb = targetFreeMb != null ? targetFreeMb + reclaimableMb : null;

  if (payload.nodeId !== targetNode.id) {
    return {
      allowed: false,
      reason: tr('僅支援同一台主機內的 GPU 之間搬移', 'Cross-host GPU migration is not supported'),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }
  if (targetNode.mode === 'demo') {
    return {
      allowed: false,
      reason: tr('模擬主機不支援模型搬移', 'Simulated hosts do not support model migration'),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }
  if (!isProviderControlReady(targetNode, payload.provider, jobs, busy)) {
    return {
      allowed: false,
      reason: tr('主機目前忙碌、離線或執行器未就緒', 'Host is busy, offline, or runtime is not ready'),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }
  if (
    payload.provider === 'lmstudio' &&
    targetNode.metrics.lmstudio?.gpu_placement_supported === false
  ) {
    return {
      allowed: false,
      reason: tr('LM Studio SDK 尚未就緒，無法指定 GPU', 'LM Studio SDK is unavailable; cannot pin GPU'),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }
  if (
    payload.provider === 'comfyui' &&
    targetNode.metrics.comfyui?.gpu_placement_supported === false
  ) {
    return {
      allowed: false,
      reason: tr('ComfyUI 未啟用 SelectModelDevice，無法指定 GPU', 'ComfyUI SelectModelDevice is unavailable; cannot pin GPU'),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }
  if (
    payload.isLoaded &&
    payload.pinnedGpuIndices?.length === 1 &&
    payload.pinnedGpuIndices[0] === targetGpuIndex
  ) {
    return {
      allowed: false,
      sameGpu: true,
      reason: tr(`已指定於 ${gpuCode(targetGpuIndex)}`, `Already pinned to ${gpuCode(targetGpuIndex)}`),
      targetFreeMb,
      effectiveFreeMb,
      estimatedVramMb: payload.estimatedVramMb,
      insufficientVram: false,
    };
  }

  let insufficientVram = false;
  let warningDetail: string | undefined;
  if (
    payload.estimatedVramMb != null &&
    effectiveFreeMb != null &&
    payload.estimatedVramMb > effectiveFreeMb
  ) {
    insufficientVram = true;
    warningDetail = tr(
      `此模型目前約佔用 ${formatDragMb(payload.estimatedVramMb)}，但 ${gpuCode(targetGpuIndex)} 預估僅餘 ${formatDragMb(effectiveFreeMb)} 可用顯存。`,
      `This model currently uses ~${formatDragMb(payload.estimatedVramMb)}, while ${gpuCode(targetGpuIndex)} has an estimated ${formatDragMb(effectiveFreeMb)} free VRAM.`,
    );
  } else if (
    payload.estimatedVramMb == null &&
    effectiveFreeMb != null &&
    effectiveFreeMb < LOW_FREE_VRAM_THRESHOLD_MB
  ) {
    insufficientVram = true;
    warningDetail = tr(
      `${gpuCode(targetGpuIndex)} 目前僅餘 ${formatDragMb(effectiveFreeMb)} 空閒顯存，直接載入可能導致顯存不足。`,
      `${gpuCode(targetGpuIndex)} only has ${formatDragMb(effectiveFreeMb)} free VRAM; loading directly may cause an OOM failure.`,
    );
  }

  return {
    allowed: true,
    targetFreeMb,
    effectiveFreeMb,
    estimatedVramMb: payload.estimatedVramMb,
    insufficientVram,
    warningDetail,
  };
}

interface ModelDragContextValue {
  dragItem: DragModelPayload | null;
  dropTarget: DropTarget | null;
  startDrag: (e: React.DragEvent, payload: DragModelPayload) => void;
  endDrag: () => void;
  setDropTarget: (target: DropTarget | null) => void;
  handleDropOnGpu: (
    e: React.DragEvent,
    node: Node,
    gpu: GPU,
    gpuIndex: number,
  ) => void;
}

const ModelDragContext = createContext<ModelDragContextValue | null>(null);

export function useModelDrag(): ModelDragContextValue {
  const ctx = useContext(ModelDragContext);
  if (!ctx) {
    throw new Error('useModelDrag must be used inside ModelDragProvider');
  }
  return ctx;
}

function VramConfirmDialog({
  pending,
  busy,
  onCancel,
  onConfirm,
}: {
  pending: PendingDropConfirm;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  const { payload, gpu, gpuIndex, evaluation } = pending;
  const shortName = gpu.name.replace(/^NVIDIA\s+(GeForce\s+)?/i, '');
  const sourceText =
    payload.sourceGpuIndex !== null
      ? gpuCode(payload.sourceGpuIndex)
      : payload.isLoaded
        ? t('目前配置', 'Current Placement')
        : t('未載入', 'Unloaded');

  return (
    <dialog
      ref={ref}
      className="gcc-dialog gcc-drag-confirm-dialog"
      aria-labelledby="drag-confirm-title"
      onCancel={e => {
        if (busy) e.preventDefault();
        else onCancel();
      }}
      onClick={e => {
        if (e.target === e.currentTarget && !busy) {
          const box = e.currentTarget.getBoundingClientRect();
          if (
            e.clientX < box.left ||
            e.clientX > box.right ||
            e.clientY < box.top ||
            e.clientY > box.bottom
          ) {
            onCancel();
          }
        }
      }}
    >
      <div className="gcc-dialog-head">
        <span>
          <WarningCircle size={18} />
          {t('顯存空間不足警示', 'Insufficient VRAM Warning')}
        </span>
        <button
          type="button"
          className="gcc-icon-button"
          aria-label={t('關閉視窗', 'Close dialog')}
          disabled={busy}
          onClick={onCancel}
        >
          <X size={20} />
        </button>
      </div>
      <div className="gcc-drag-confirm-body">
        <h2 id="drag-confirm-title">{t('目標顯示卡空閒顯存可能不足', 'Target GPU may have insufficient free VRAM')}</h2>
        <p className="gcc-drag-confirm-desc">
          {evaluation.warningDetail || t('目標顯示卡目前可用顯存偏低，載入過程可能失敗或溢位至系統記憶體。', 'Target GPU has low available VRAM; loading may fail or spill into system RAM.')}
        </p>
        <dl className="gcc-drag-confirm-summary">
          <div>
            <dt>{t('模型', 'Model')}</dt>
            <dd>
              <strong>{payload.displayName}</strong>
              <span>
                {payload.provider === 'lmstudio'
                  ? pending.node.metrics.lmstudio?.backend === 'direct_llama'
                    ? t('GGUF 直載', 'Direct GGUF')
                    : 'LM Studio'
                  : 'ComfyUI'}
              </span>
            </dd>
          </div>
          <div>
            <dt>{t('搬移方向', 'Migration')}</dt>
            <dd>
              <code>{sourceText}</code>
              <span aria-hidden="true">→</span>
              <code className="is-target">
                {gpuCode(gpuIndex)} · {shortName}
              </code>
            </dd>
          </div>
          <div>
            <dt>{t('顯存對比', 'VRAM Comparison')}</dt>
            <dd>
              {evaluation.estimatedVramMb != null && (
                <span>
                  {t('預估模型', 'Est. Model')} <strong>{formatDragMb(evaluation.estimatedVramMb)}</strong>
                </span>
              )}
              <span>
                {t('目標可用', 'Target Free')} <strong>{formatDragMb(evaluation.effectiveFreeMb)}</strong>
              </span>
            </dd>
          </div>
        </dl>
        <p className="gcc-drag-confirm-note">
          {payload.provider === 'comfyui'
            ? t('執行時會先清空 ComfyUI 既有快取，再預載至目標 GPU。', 'ComfyUI cache will be cleared first before preloading onto the target GPU.')
            : payload.replace
              ? t('執行時會先卸載既有實例，再透過 LM Studio SDK 指定新 GPU 重新載入。', 'Existing instance will be unloaded before reloading onto the new GPU.')
              : t('執行時會透過 LM Studio SDK 指定載入至目標 GPU。', 'Model will be loaded and pinned to the target GPU.')}
        </p>
      </div>
      <div className="gcc-create-actions">
        <span>
          <ArrowsLeftRight size={15} />
          {t('中央佇列依序執行', 'Dispatched via central queue')}
        </span>
        <div>
          <button type="button" className="gcc-button" disabled={busy} onClick={onCancel}>
            {t('取消', 'Cancel')}
          </button>
          <button
            type="button"
            className="gcc-button gcc-button--warning"
            disabled={busy}
            onClick={onConfirm}
          >
            {payload.isLoaded ? t(`仍要搬移至 ${gpuCode(gpuIndex)}`, `Move to ${gpuCode(gpuIndex)} Anyway`) : t(`仍要載入至 ${gpuCode(gpuIndex)}`, `Load onto ${gpuCode(gpuIndex)} Anyway`)}
          </button>
        </div>
      </div>
    </dialog>
  );
}

export function ModelDragProvider({
  jobs,
  busy,
  onModelAction,
  children,
}: {
  jobs: Job[];
  busy: boolean;
  onModelAction: ModelActionHandler;
  children: React.ReactNode;
}) {
  const { locale } = useI18n();
  const [dragItem, setDragItem] = useState<DragModelPayload | null>(null);
  const [dropTarget, setDropTarget] = useState<DropTarget | null>(null);
  const [pendingConfirm, setPendingConfirm] = useState<PendingDropConfirm | null>(null);
  const dragRef = useRef<DragModelPayload | null>(null);

  const startDrag = useCallback((e: React.DragEvent, payload: DragModelPayload) => {
    if (isInteractiveDragOrigin(e.target)) {
      e.preventDefault();
      return;
    }
    dragRef.current = payload;
    setDragItem(payload);
    setDropTarget(null);
    e.dataTransfer.effectAllowed = 'move';
    try {
      e.dataTransfer.setData(DRAG_MIME, JSON.stringify(payload));
      e.dataTransfer.setData('text/plain', payload.displayName);
    } catch {
      // Ignore restricted dataTransfer environments.
    }
  }, []);

  const endDrag = useCallback(() => {
    dragRef.current = null;
    setDragItem(null);
    setDropTarget(null);
  }, []);

  const handleDropOnGpu = useCallback(
    (e: React.DragEvent, node: Node, gpu: GPU, gpuIndex: number) => {
      e.preventDefault();
      let payload = dragRef.current;
      if (!payload) {
        try {
          const raw = e.dataTransfer.getData(DRAG_MIME);
          if (raw) payload = JSON.parse(raw) as DragModelPayload;
        } catch {
          payload = null;
        }
      }
      dragRef.current = null;
      setDragItem(null);
      setDropTarget(null);
      if (!payload) return;

      const evaluation = evaluateGpuDrop(payload, node, gpu, gpuIndex, jobs, busy, locale);
      if (!evaluation.allowed) return;
      if (evaluation.insufficientVram) {
        setPendingConfirm({ node, gpu, gpuIndex, payload, evaluation });
        return;
      }
      onModelAction(node, payload.modelId, 'load', [gpuIndex], payload.replace);
    },
    [busy, jobs, locale, onModelAction],
  );

  const confirmPending = useCallback(() => {
    if (!pendingConfirm) return;
    const { node, gpuIndex, payload } = pendingConfirm;
    setPendingConfirm(null);
    onModelAction(node, payload.modelId, 'load', [gpuIndex], payload.replace);
  }, [onModelAction, pendingConfirm]);

  const value = useMemo<ModelDragContextValue>(
    () => ({
      dragItem,
      dropTarget,
      startDrag,
      endDrag,
      setDropTarget,
      handleDropOnGpu,
    }),
    [dragItem, dropTarget, endDrag, handleDropOnGpu, startDrag],
  );

  return (
    <ModelDragContext.Provider value={value}>
      {children}
      {pendingConfirm && (
        <VramConfirmDialog
          pending={pendingConfirm}
          busy={busy}
          onCancel={() => setPendingConfirm(null)}
          onConfirm={confirmPending}
        />
      )}
    </ModelDragContext.Provider>
  );
}

/** Right-rail live GPU VRAM snapshot card with drag-and-drop targets & draggable resident pills. */
export function HostVramDropCard({
  node,
  jobs,
  busy,
}: {
  node: Node;
  jobs: Job[];
  busy: boolean;
}) {
  const { locale, t } = useI18n();
  const { dragItem, dropTarget, startDrag, endDrag, setDropTarget, handleDropOnGpu } = useModelDrag();
  const gpus = node.metrics.gpus || [];
  if (node.mode === 'demo' || !gpus.length) return null;

  const activeJob = findHostActiveJob(node, jobs);
  const canDragLms =
    node.metrics.lmstudio?.gpu_placement_supported !== false &&
    isProviderControlReady(node, 'lmstudio', jobs, busy);
  const canDragComfy =
    node.metrics.comfyui?.gpu_placement_supported !== false &&
    isProviderControlReady(node, 'comfyui', jobs, busy);
  const comfyPreloaded = getComfyPreloadedInfo(node, jobs);
  const comfyLoadableSet = useMemo(
    () => new Set((node.model_catalog || []).filter(m => m.runtime === 'comfyui').map(m => m.name)),
    [node.model_catalog],
  );
  const isDraggingThisHost = dragItem?.nodeId === node.id;

  return (
    <section
      className={`gcc-host-vram-card${isDraggingThisHost ? ' is-host-dragging' : ''}`}
      aria-label={t(`${node.name} GPU 顯存快照`, `${node.name} GPU VRAM snapshot`)}
    >
      <div className="gcc-vram-card-head">
        <Cpu size={18} />
        <div>
          <h4>{t('GPU 顯存即時快照', 'Live GPU VRAM Snapshot')}</h4>
          <p className="gcc-vram-drag-hint">
            {isDraggingThisHost
              ? t(`將「${dragItem.displayName}」放開於下方目標 GPU`, `Drop "${dragItem.displayName}" onto a target GPU below`)
              : t('可拖曳左側模型或卡片標籤至此處指定 GPU', 'Drag any model row or badge here to pin to a GPU')}
          </p>
        </div>
        <span>{gpus.length} {t('張', gpus.length === 1 ? 'GPU' : 'GPUs')}</span>
      </div>
      <ul className="gcc-vram-list">
        {gpus.map((gpu, i) => {
          const idx = gpu.index ?? i;
          const hasMem =
            gpu.memory_total_mb != null &&
            gpu.memory_used_mb != null &&
            gpu.memory_total_mb > 0;
          const ratio = hasMem
            ? Math.min(100, Math.max(0, (gpu.memory_used_mb! / gpu.memory_total_mb!) * 100))
            : null;
          const usedGb = hasMem ? (gpu.memory_used_mb! / 1024).toFixed(1) : '—';
          const totalGb = hasMem ? (gpu.memory_total_mb! / 1024).toFixed(0) : '—';
          const freeGb = hasMem
            ? Math.max(0, (gpu.memory_total_mb! - gpu.memory_used_mb!) / 1024).toFixed(1)
            : null;

          const lmsOnCard = (node.metrics.lmstudio?.loaded_models || [])
            .filter(m => m.observed_gpus?.some(og => og.index === idx))
            .map(m => ({
              provider: 'lmstudio' as const,
              model: m,
              id: `lmstudio:${m.name}`,
              label: modelDisplayName(node, `lmstudio:${m.name}`, m.name),
            }));
          const ollamaOnCard = (node.metrics.ollama?.loaded_models || [])
            .filter(m => m.observed_gpus?.some(og => og.index === idx))
            .map(m => ({
              provider: 'ollama' as const,
              model: m,
              id: `ollama:${m.name}`,
              label: modelDisplayName(node, `ollama:${m.name}`, m.name),
            }));

          const comfyModelsOnCard = new Set<string>();
          let comfyFallbackPrimary: string | null = null;
          if (comfyPreloaded.model && comfyPreloaded.gpuIndex === idx && comfyLoadableSet.has(comfyPreloaded.model)) {
            comfyModelsOnCard.add(comfyPreloaded.model);
          }
          for (const proc of gpu.other_processes || []) {
            if (proc.category === 'comfyui') {
              for (const pm of proc.models || []) {
                if (comfyLoadableSet.has(pm.name)) comfyModelsOnCard.add(pm.name);
                else if (!comfyFallbackPrimary) comfyFallbackPrimary = pm.name;
              }
            }
          }

          const trackedMb = [...lmsOnCard, ...ollamaOnCard].reduce(
            (sum, item) =>
              sum + (item.model.observed_gpus?.find(og => og.index === idx)?.memory_used_mb ?? 0),
            0,
          );
          const otherMb = hasMem ? Math.max(0, gpu.memory_used_mb! - trackedMb) : 0;
          const otherLabel =
            otherMb >= 1024 ? `${(otherMb / 1024).toFixed(1)} GiB` : `${Math.round(otherMb)} MiB`;

          const evaluation = dragItem
            ? evaluateGpuDrop(dragItem, node, gpu, idx, jobs, busy, locale)
            : null;
          const isOver =
            dropTarget?.nodeId === node.id && dropTarget.gpuIndex === idx;
          const isDropReady = Boolean(dragItem && evaluation?.allowed);
          const isIncoming = Boolean(
            activeJob &&
              activeJob.parameters.model_action === 'load' &&
              activeJob.parameters.gpu_indices?.includes(idx),
          );
          const incomingLabel = isIncoming && activeJob
            ? modelDisplayName(
                node,
                activeJob.model,
                activeJob.model.replace(/^(lmstudio|ollama|comfyui):/, ''),
              )
            : null;

          const rowClass = [
            ratio !== null && ratio > 90 ? 'is-high' : '',
            isDropReady ? 'is-drop-ready' : '',
            isOver && evaluation?.allowed
              ? evaluation.insufficientVram
                ? 'is-drop-warning'
                : 'is-drop-over'
              : '',
            isOver && evaluation && !evaluation.allowed ? 'is-drop-blocked' : '',
          ]
            .filter(Boolean)
            .join(' ');

          return (
            <li
              key={gpu.uuid || idx}
              className={rowClass}
              onDragOver={e => {
                if (!dragItem) return;
                e.preventDefault();
                e.dataTransfer.dropEffect = evaluation?.allowed ? 'move' : 'none';
                if (!isOver) setDropTarget({ nodeId: node.id, gpuIndex: idx });
              }}
              onDragLeave={e => {
                if (!e.currentTarget.contains(e.relatedTarget as globalThis.Node | null)) {
                  if (isOver) setDropTarget(null);
                }
              }}
              onDrop={e => handleDropOnGpu(e, node, gpu, idx)}
            >
              <div className="gcc-vram-row-top">
                <strong>
                  {gpuCode(idx)}
                  <span>{gpu.name.replace(/^NVIDIA\s+(GeForce\s+)?/i, '')}</span>
                </strong>
                <code>
                  {usedGb} / {totalGb} GB
                </code>
              </div>
              <div
                className="gcc-vram-bar"
                role="meter"
                aria-label={t(`GPU ${idx} 顯存使用率`, `GPU ${idx} VRAM utilization`)}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={ratio === null ? undefined : Math.round(ratio)}
              >
                <span style={{ transform: `scaleX(${(ratio ?? 0) / 100})` }} />
              </div>
              <div className="gcc-vram-row-sub">
                <span>{freeGb !== null ? t(`空閒 ${freeGb} GB`, `${freeGb} GB free`) : t('等待顯存資料', 'Waiting for telemetry')}</span>
                <span>
                  {gpu.utilization != null ? t(`負載 ${Math.round(gpu.utilization)}%`, `Load ${Math.round(gpu.utilization)}%`) : '—'}
                  {gpu.temperature_c != null ? ` · ${gpu.temperature_c}°C` : ''}
                </span>
              </div>

              {dragItem && isOver && evaluation && (
                <div
                  className={`gcc-drop-banner${
                    !evaluation.allowed
                      ? ' is-blocked'
                      : evaluation.insufficientVram
                        ? ' is-warning'
                        : ' is-ready'
                  }`}
                >
                  {!evaluation.allowed ? (
                    <span>{evaluation.reason}</span>
                  ) : evaluation.insufficientVram ? (
                    <>
                      <WarningCircle size={14} />
                      <span>
                        {t(`顯存可能不足（模型約 ${formatDragMb(evaluation.estimatedVramMb)} / 可用 ${formatDragMb(evaluation.effectiveFreeMb)}），放開後確認`, `Low VRAM (model ~${formatDragMb(evaluation.estimatedVramMb)} / free ${formatDragMb(evaluation.effectiveFreeMb)}). Drop to confirm.`)}
                      </span>
                    </>
                  ) : (
                    <>
                      {dragItem.isLoaded ? <ArrowsLeftRight size={14} /> : <DownloadSimple size={14} />}
                      <span>
                        {t(`放開以${dragItem.isLoaded ? '搬移' : '載入'}至 ${gpuCode(idx)}${evaluation.effectiveFreeMb != null ? `（可用 ${formatDragMb(evaluation.effectiveFreeMb)}）` : ''}`, `Drop to ${dragItem.isLoaded ? 'move' : 'load'} onto ${gpuCode(idx)}${evaluation.effectiveFreeMb != null ? ` (${formatDragMb(evaluation.effectiveFreeMb)} free)` : ''}`)}
                      </span>
                    </>
                  )}
                </div>
              )}

              {(lmsOnCard.length > 0 ||
                ollamaOnCard.length > 0 ||
                comfyModelsOnCard.size > 0 ||
                comfyFallbackPrimary ||
                isIncoming ||
                otherMb >= 64) && (
                <div className="gcc-vram-residents">
                  {lmsOnCard.map((item, mi) => {
                    const draggable = canDragLms && gpus.length > 1;
                    const isSelfDragging =
                      dragItem?.nodeId === node.id && dragItem.modelId === item.id;
                    const isMovingOut = Boolean(
                      activeJob &&
                        activeJob.model === item.id &&
                        activeJob.parameters.model_action === 'load' &&
                        !activeJob.parameters.gpu_indices?.includes(idx),
                    );
                    return (
                      <code
                        key={`${item.id}-${mi}`}
                        draggable={draggable}
                        className={`${draggable ? 'is-draggable-chip' : ''}${
                          isSelfDragging ? ' is-dragging' : ''
                        }${isMovingOut ? ' is-moving-out' : ''}`}
                        title={
                          draggable
                            ? t(`拖曳 ${item.label} 到其他 GPU 卡片以搬移`, `Drag ${item.label} to another GPU card to relocate`)
                            : t('主機忙碌或僅單張 GPU，暫時無法拖曳', 'Host is busy or single-GPU; drag unavailable')
                        }
                        onDragStart={e => {
                          if (!draggable) return;
                          startDrag(e, buildLmStudioDragPayload(node, item.model.name, item.model, idx));
                        }}
                        onDragEnd={endDrag}
                      >
                        {draggable && <DotsSixVertical size={11} className="gcc-drag-handle-icon" />}
                        {item.label}
                        {isMovingOut && t(' · 移出中', ' · Moving')}
                      </code>
                    );
                  })}
                  {ollamaOnCard.map((item, mi) => (
                    <code
                      key={`${item.id}-${mi}`}
                      className="is-non-draggable-chip"
                      title={t('Ollama 目前不支援逐模型指定 GPU 搬移', 'Ollama does not currently support per-model GPU migration')}
                    >
                      {item.label}
                    </code>
                  ))}
                  {Array.from(comfyModelsOnCard).map(file => {
                    const modelId = `comfyui:${file}`;
                    const label = modelDisplayName(node, modelId, file);
                    const draggable = canDragComfy && gpus.length > 1;
                    const isSelfDragging =
                      dragItem?.nodeId === node.id && dragItem.modelId === modelId;
                    return (
                      <code
                        key={modelId}
                        draggable={draggable}
                        className={`is-comfy-chip${draggable ? ' is-draggable-chip' : ''}${
                          isSelfDragging ? ' is-dragging' : ''
                        }`}
                        title={
                          draggable
                            ? t(`拖曳 ComfyUI 模型 ${label} 到其他 GPU 卡片以切換裝置`, `Drag ComfyUI model ${label} to another GPU card to switch device`)
                            : t('ComfyUI 目前無法切換裝置', 'ComfyUI device switching unavailable')
                        }
                        onDragStart={e => {
                          if (!draggable) return;
                          startDrag(e, buildComfyDragPayload(node, file, jobs, idx));
                        }}
                        onDragEnd={endDrag}
                      >
                        {draggable && <DotsSixVertical size={11} className="gcc-drag-handle-icon" />}
                        {label}
                      </code>
                    );
                  })}
                  {comfyModelsOnCard.size === 0 && comfyFallbackPrimary && (
                    <code className="is-comfy" title={comfyFallbackPrimary}>
                      ComfyUI · {modelDisplayName(node, `comfyui:${comfyFallbackPrimary}`, comfyFallbackPrimary)}
                    </code>
                  )}
                  {isIncoming && incomingLabel && (
                    <code className="is-incoming-chip">
                      <CircleNotch size={11} className="gcc-spin" />
                      {incomingLabel} · {activeJob?.status === 'queued' ? t('排隊中', 'Queued') : t('載入中', 'Loading')}
                    </code>
                  )}
                  {otherMb >= 64 && <code className="is-other">{t('其他程序', 'Other')} {otherLabel}</code>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
