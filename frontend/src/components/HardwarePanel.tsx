import { Cpu, Database, DesktopTower, Lightning, Pause, Play, Thermometer, Queue, CheckCircle } from '@phosphor-icons/react';
import type { GPU, Job, Node } from '../api';
import { Badge, Empty, time } from './ui';
import {ModelControls, type ModelActionHandler, type RuntimeHandler} from './ModelControls';
import {ComfyPanel} from './ComfyPanel';

const valid = (value?: number | null): value is number => value != null && Number.isFinite(value);
const percent = (value?: number | null) => valid(value) ? `${Math.round(value)}%` : '—';
const memory = (value?: number | null) => valid(value) ? (value / 1024).toFixed(1) : '—';
function LoadRing({value,label,compact=false}:{value:number|null|undefined;label:string;compact?:boolean}) {
  const amount=valid(value) ? Math.max(0,Math.min(100,value)) : 0;
  return <div className={`gcc-load-ring${compact ? ' is-compact' : ''}`} role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={valid(value) ? Math.round(amount) : undefined} aria-valuetext={valid(value) ? `${Math.round(amount)}%` : '無資料'}>
    <svg viewBox="0 0 100 100" aria-hidden="true"><circle className="gcc-ring-track" cx="50" cy="50" r="42"/><circle className="gcc-ring-fill" cx="50" cy="50" r="42" pathLength="100" strokeDasharray={`${amount} 100`} transform="rotate(-90 50 50)"/></svg>
    <div><strong>{valid(value) ? Math.round(value) : '—'}<small>{valid(value) ? '%' : ''}</small></strong><span>{label}</span></div>
  </div>;
}

function GraphicsCard() {
  return <svg className="gcc-card-illustration" viewBox="0 0 320 124" role="img" aria-label="顯示卡外形示意圖，非實際風扇轉速或硬體照片">
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

function GpuDevice({gpu,index,simulated,offline}:{gpu:GPU;index:number;simulated:boolean;offline:boolean}) {
  const ratio=valid(gpu.memory_used_mb) && valid(gpu.memory_total_mb) && gpu.memory_total_mb>0 ? gpu.memory_used_mb/gpu.memory_total_mb*100 : null;
  const highMemory=ratio!==null && ratio>90;
  return <section className={`gcc-device${highMemory ? ' is-memory-high' : ''}${offline ? ' is-stale' : ''}`} aria-label={`GPU ${gpu.index ?? index} ${gpu.name}`}>
    <div className="gcc-device-head"><div><span className="gcc-device-label">GPU {String(gpu.index ?? index).padStart(2,'0')}</span><h4>{gpu.name.replace('NVIDIA GeForce ','')}</h4></div><span className="gcc-device-state">{offline ? '最後數據' : !valid(gpu.utilization) ? '無數據' : gpu.utilization>=90 ? '高負載' : gpu.utilization>5 ? '使用中' : '低負載'}</span></div>
    <GraphicsCard/>
    <div className="gcc-device-readings"><LoadRing value={gpu.utilization} label="運算負載"/><div className="gcc-memory-readout"><span><Database size={16}/>顯存容量</span><strong>{memory(gpu.memory_used_mb)}<small> / {memory(gpu.memory_total_mb)} GB</small></strong><div className="gcc-memory-cells" role="meter" aria-label={`GPU ${index} 顯存使用率`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={ratio===null ? undefined : Math.round(ratio)}>{Array.from({length:12},(_,i) => <span key={i}><i style={{transform:`scaleY(${ratio===null ? 0 : Math.max(0,Math.min(1,ratio/100*12-i))})`}}/></span>)}</div><small>{ratio===null ? '等待顯存資料' : highMemory ? `已使用 ${Math.round(ratio)}% · 容量接近滿載` : `已使用 ${Math.round(ratio)}%`}</small></div></div>
    <div className="gcc-device-vitals"><div><Thermometer size={21}/><span>溫度<strong>{gpu.temperature_c ?? '—'}<small>°C</small></strong></span></div><div><Lightning size={21}/><span>功耗<strong>{valid(gpu.power_w) ? Math.round(gpu.power_w) : '—'}<small>W</small></strong></span></div></div>
    {simulated && <span className="gcc-device-simulated">模擬資料</span>}
  </section>;
}

export function HardwarePanel({nodes,jobs,busy,onMaintenance,onModelAction,onPreference}:{nodes:Node[];jobs:Job[];busy:boolean;onMaintenance:(node:Node)=>void;onModelAction:ModelActionHandler;onPreference:RuntimeHandler}) {
  const gpuCount=nodes.reduce((sum,n)=>sum+(n.metrics.gpus?.length || 0),0);
  return <section className="gcc-panel gcc-hardware-panel" aria-labelledby="hardware-title"><div className="gcc-panel-head"><div><h2 id="hardware-title">你的運算設備</h2><p>{nodes.length} 台主機 · {gpuCount} 張顯示卡</p></div><span className="gcc-subtle">每 2 秒更新</span></div>
    {!nodes.length ? <Empty icon={<DesktopTower size={32}/>} title="等待設備連線">啟動主機 Agent 後，硬體狀態會自動出現在這裡。</Empty> : nodes.map(node=><article className="gcc-host" key={node.id}>
      <div className="gcc-host-head"><div className="gcc-host-name"><span className="gcc-host-symbol"><DesktopTower size={25}/></span><div><h3>{node.name}</h3><p>{node.mode==='demo' ? '模擬設備' : '你的本機運算主機'}</p></div></div><Badge value={node.status}/></div>
      <ModelControls key={node.default_runtime} compact node={node} jobs={jobs} busy={busy} onAction={onModelAction} onPreference={onPreference}/>
      <div className="gcc-device-grid">{node.metrics.gpus?.length ? node.metrics.gpus.map((gpu,i)=><GpuDevice gpu={gpu} index={i} simulated={node.mode==='demo'} offline={node.status==='offline'} key={gpu.uuid || i}/>) : <p className="gcc-muted gcc-no-gpu">尚未偵測到顯示卡。</p>}</div>
      <div className="gcc-system-resources"><div className="gcc-system-resource"><span className="gcc-chip-visual"><Cpu size={34}/><span>{Array.from({length:9},(_,i)=><i key={i} className={valid(node.metrics.cpu_percent) && i/9*100<node.metrics.cpu_percent ? 'is-filled' : ''}/>)}</span></span><div><span>處理器 CPU</span><strong>{percent(node.metrics.cpu_percent)}<small>使用中</small></strong></div></div><div className="gcc-system-resource"><span className="gcc-ram-visual" aria-hidden="true">{Array.from({length:5},(_,i)=><i key={i} className={valid(node.metrics.ram_percent) && i/5*100<node.metrics.ram_percent ? 'is-filled' : ''}/>)}</span><div><span>系統記憶體</span><strong>{percent(node.metrics.ram_percent)}<small>{node.metrics.ram_total_gb ? `/ ${node.metrics.ram_total_gb} GB` : '使用中'}</small></strong></div></div></div>
      {node.mode!=='demo' && <ComfyPanel node={node}/>}
      <div className="gcc-host-footer"><p className="gcc-heartbeat">{node.status==='offline' ? '顯示最後收到的數據' : '最後更新'} <time dateTime={node.last_seen}>{time(node.last_seen)}</time></p><button className="gcc-button gcc-button--quiet" disabled={busy} onClick={()=>onMaintenance(node)}>{node.maintenance ? <Play size={16}/> : <Pause size={16}/>}{node.maintenance ? '恢復接單' : '停止接單'}</button></div>
    </article>)}
  </section>;
}

export function SlotPanel({nodes,jobs}:{nodes:Node[];jobs:Job[]}) {
  const active=jobs.filter(j=>['running','cancelling','unknown'].includes(j.status));
  return <section className="gcc-panel gcc-slots" aria-labelledby="slots-title"><div className="gcc-panel-head"><div><h2 id="slots-title">工作分配</h2><p>看看主機正在忙什麼</p></div></div><div className="gcc-flow" aria-hidden="true"><Queue size={25}/><span/><Cpu size={29}/><span/><CheckCircle size={25}/></div>{nodes.length ? nodes.map(node=>{
    const holders=active.filter(j=>j.node_id===node.id);
    return <div className="gcc-slot" key={node.id}><div className="gcc-slot-label"><span>{node.name}</span><span className={holders.length ? 'gcc-accent-text' : 'gcc-muted'}>{holders.length ? '工作中' : node.status==='online' ? '待命中' : '未接單'}</span></div>{holders.length ? holders.map(j=><div key={j.id} className="gcc-slot-job"><strong>{j.name}</strong><span>{j.source} · {j.model.replace('ollama:','')}</span><Badge value={j.status}/></div>) : <p className="gcc-slot-idle">{node.status==='online' ? '目前沒有中央任務，可以開始新的推論。' : '等待主機恢復在線或接單。'}</p>}</div>;
  }) : <p className="gcc-slot-idle">尚無已登錄主機。</p>}<div className="gcc-slot-note"><span className="gcc-keyline"/>每台主機一次處理一個中央任務；硬體數據也包含其他程序的用量。</div></section>;
}
