import {useState} from 'react';
import { CircleNotch, DownloadSimple, Eject, Stack } from '@phosphor-icons/react';
import type { Job, Node } from '../api';

export type RuntimeHandler = (node:Node, runtime:'lmstudio'|'ollama')=>void;
export type ModelActionHandler = (node:Node, model:string, action:'load'|'unload')=>void;
const size = (bytes?:number|null) => bytes==null ? '—' : `${(bytes/1024**3).toFixed(2)} GiB`;

export function ModelControls({node,jobs,busy,onAction,onPreference,compact=false}:{node:Node;jobs:Job[];busy:boolean;onAction:ModelActionHandler;onPreference:RuntimeHandler;compact?:boolean}) {
  const [selection,setSelection]=useState('');
  const provider=node.default_runtime || 'lmstudio';
  const runtime=node.metrics[provider];
  const providerName=provider==='lmstudio' ? 'LM Studio' : 'Ollama';
  const known=runtime?.loaded_models_status==='online' && Array.isArray(runtime.loaded_models);
  const residents=runtime?.loaded_models || [];
  const models=new Map((node.model_catalog || []).filter(m=>m.runtime===provider).map(m=>[m.id,{id:m.id,name:m.name,installed:true}]));
  residents.forEach(m=>{if(!models.has(`${provider}:${m.name}`)) models.set(`${provider}:${m.name}`,{id:`${provider}:${m.name}`,name:m.name,installed:false});});
  const allModels=Array.from(models.values());
  const selected=models.get(selection) || models.get(`${provider}:${residents[0]?.name}`) || allModels[0];
  const visibleModels=compact ? selected ? [selected] : [] : allModels;
  const latest=jobs.find(j=>j.model.startsWith(provider+':') && j.parameters.model_action && j.parameters.target_node_id===node.id);
  const active=jobs.find(j=>(j.node_id===node.id || j.parameters.target_node_id===node.id) && (['running','cancelling','unknown'].includes(j.status) || (j.status==='queued' && !!j.parameters.model_action)));
  const syncing=latest?.status==='completed' && !!latest.finished_at && latest.finished_at>node.last_seen;
  const disabled=busy || !!active || syncing || node.status!=='online' || !known || !node.capabilities.includes(`${provider}.model_control`);
  return <section className="gcc-loaded-models" aria-label={`${node.name} 模型控制`}>
    <div className="gcc-loaded-heading"><Stack size={22}/><div><h4>模型載入控制</h4><p>{node.status==='offline' ? '主機離線 · 以下為最後回報狀態' : '選擇執行器，直接載入或卸載模型'}</p></div><span className="gcc-badge">{known ? residents.length : '—'} 已載入</span></div>
    {node.mode!=='demo' && <label className="gcc-runtime-select"><span>預設執行器</span><select value={provider} disabled={busy} onChange={e=>onPreference(node,e.target.value as 'lmstudio'|'ollama')}><option value="lmstudio">LM Studio</option><option value="ollama">Ollama</option></select><small>選擇會儲存到此主機，推論表單也會優先使用。</small></label>}
    {node.mode==='demo'  ? <p className="gcc-muted">模擬主機不提供真實模型控制。</p> : <>
      {!known && <p className="gcc-muted">{runtime?.loaded_models_status==='unavailable' ? '無法取得執行器狀態，等待重新連線。' : '等待 Agent 回報模型載入狀態。'}</p>}
      {known && !residents.length && <p className="gcc-muted">目前沒有常駐模型，選擇下方模型即可載入。</p>}
      {compact && !!residents.length && <div className="gcc-resident-chips"><span>目前載入</span>{residents.map((m,i)=><code key={`${m.name}-${i}`}>{m.name}</code>)}</div>}
      {compact && !!allModels.length && <label className="gcc-runtime-select"><span>選擇模型</span><select value={selected?.id || ''} onChange={e=>setSelection(e.target.value)}>{allModels.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>}
      <ul className="gcc-control-models">{visibleModels.map(model=>{
        const resident=residents.find(m=>m.name===model.name);
        const operation=active?.model===model.id ? active.parameters.model_action : undefined;
        return <li key={model.id}><div className="gcc-control-model-info"><strong>{model.name}</strong><span>{resident ? provider==='lmstudio' ? '已載入 · 顯存大小未由服務提供' : `已載入 · GPU 顯存 ${size(resident.vram_bytes)} · 載入大小 ${size(resident.size_bytes)}` : known ? '未載入' : '載入狀態未知'}</span></div>
          <button className={`gcc-button${resident ? '' : ' gcc-button--primary'}`} disabled={disabled} aria-label={`${resident ? '卸載' : '載入'} ${model.name}`} onClick={()=>onAction(node,model.id,resident ? 'unload' : 'load')}>
            {operation ? <CircleNotch size={17} className="gcc-spin"/> : resident ? <Eject size={17}/> : <DownloadSimple size={17}/>}{operation ? active?.status==='queued' ? '等待派送' : operation==='load' ? '載入中' : '卸載中' : resident ? '卸載' : '載入'}
          </button></li>;
      })}</ul>
      {!models.size && <p className="gcc-muted">尚無已安裝模型，請確認執行器已啟動並安裝模型。</p>}
      <div role="status" className="gcc-model-operation-status">{active ? active.status==='unknown' ? '上次操作狀態待確認，已保留主機工作槽。' : active.parameters.model_action ? `${active.name}：${active.status==='queued' ? '等待 Agent' : '正在處理，請稍候'}。` : '主機正在執行推論，完成後即可操作模型。' : syncing ? '操作完成，正在同步模型狀態…' : latest?.status==='failed' ? `操作失敗：${latest.error || '請查看任務紀錄。'}` : latest?.status==='completed' ? `${latest.name}：已完成。` : ''}</div>
    </>}
    <p className="gcc-loaded-note">{node.maintenance ? '主機已停止接單，恢復後才能操作。' : !node.capabilities.includes(`${provider}.model_control`) ? '請更新主機 Agent 以啟用模型控制。' : `${providerName} 的操作由中央排程；卸載會移除此模型的所有實例。${provider==='lmstudio' ? '常駐時間仍受 LM Studio 設定影響。' : '手動載入後保持常駐，直到卸載。'}`}</p>
  </section>;
}
