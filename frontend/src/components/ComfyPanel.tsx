import { FlowArrow, Queue, Stack } from '@phosphor-icons/react';
import type { Node } from '../api';
import {Badge} from './ui';

export function ComfyPanel({node}:{node:Node}) {
  const comfy=node.metrics.comfyui;
  return <section className="gcc-comfy" aria-label={`${node.name} ComfyUI 狀態`}>
    <div className="gcc-comfy-head"><FlowArrow size={23}/><h4>ComfyUI</h4><Badge value={node.status==='offline' || comfy?.status!=='online' ? 'offline' : 'online'}/>{comfy?.version && <span className="gcc-muted">v{comfy.version}</span>}</div>
    {!comfy || comfy.status!=='online' ? <p className="gcc-muted">{comfy ? '無法連接 ComfyUI，請確認服務與 Agent 的 COMFYUI_URL 設定。' : '等待 Agent 讀取 ComfyUI。'}</p> : <>
      {node.status==='offline' && <p className="gcc-muted">主機離線，以下為最後回報資料。</p>}
      <div className="gcc-comfy-counts"><span><Queue size={18}/>執行中 <strong>{comfy.queue_status==='online' ? comfy.running?.length ?? 0 : '—'}</strong></span><span>等待中 <strong>{comfy.queue_status==='online' ? comfy.pending?.length ?? 0 : '—'}</strong></span></div>
      {comfy.queue_status!=='online' ? <p className="gcc-muted">暫時無法取得佇列。</p> : ((comfy.running?.length || 0)+(comfy.pending?.length || 0)>0) && <details className="gcc-disclosure"><summary>查看服務佇列</summary><div><ul className="gcc-comfy-queue">{comfy.running?.map(j=><li key={j.id}>執行中 <code>{j.id}</code></li>)}{comfy.pending?.map(j=><li key={j.id}>等待中 <code>{j.id}</code></li>)}</ul></div></details>}
      <details className="gcc-disclosure"><summary><Stack size={18}/>可用模型清單</summary><div>{comfy.models_status==='unavailable' ? <p>暫時無法取得模型清單。</p> : <>{comfy.models_status==='partial' && <p>部分分類無法讀取或超出顯示上限。</p>}{comfy.model_groups?.map(group=><details className="gcc-disclosure" key={group.folder}><summary>{group.folder}<span>{group.status==='online' ? group.count : '無法讀取'}</span></summary><div>{group.files.length ? <ul className="gcc-comfy-models">{group.files.map(file=><li key={file}>{file}</li>)}</ul> : <p>此分類沒有模型。</p>}{group.truncated && <p>僅顯示前 200 筆。</p>}</div></details>)}</>}</div></details>
      {comfy.devices?.map((device,i)=><p className="gcc-loaded-note" key={i}>{device.name} · 服務回報可用顯存 {(device.vram_free/1024**3).toFixed(1)} / {(device.vram_total/1024**3).toFixed(1)} GiB</p>)}
      <p className="gcc-loaded-note">僅讀取此 ComfyUI 服務；模型清單代表可用檔案，並非已載入模型。佇列獨立於中央推論任務。</p>
    </>}
  </section>;
}
