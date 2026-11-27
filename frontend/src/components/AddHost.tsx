import {useEffect, useRef, useState} from 'react';
import {Check, CircleNotch, DesktopTower, Eye, EyeSlash, LockSimple, WarningCircle, X} from '@phosphor-icons/react';
import {api, type Node} from '../api';
import {useI18n} from '../i18n';

type Service = {provider:string;url:string;model_count:number;latency_ms:number};
type Result = {ok:boolean;message:string;services?:Service[];checks?:{provider:string;ok:boolean;reason?:string}[]};
const labels:Record<string,string>={lmstudio:'LM Studio',ollama:'Ollama',comfyui:'ComfyUI'};

export function AddHost({nodes,onClose,onAdded}:{nodes:Node[];onClose:()=>void;onAdded:()=>Promise<void>}) {
  const { t, translateApiText } = useI18n();
  const ref=useRef<HTMLDialogElement>(null);
  const request=useRef(0);
  const [name,setName]=useState('');
  const [address,setAddress]=useState('');
  const [password,setPassword]=useState('');
  const [confirmPassword,setConfirmPassword]=useState('');
  const [showPassword,setShowPassword]=useState(false);
  const [testing,setTesting]=useState(false);
  const [saving,setSaving]=useState(false);
  const [result,setResult]=useState<Result|null>(null);
  const [error,setError]=useState('');
  const validName=/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,99}$/.test(name.trim());
  const existing=nodes.some(node=>node.id===name.trim());
  const hasPassword=password.length>0;
  const validPassword=!hasPassword || (password.length>=4 && password.trim().length>0 && password.length<=128);
  const passwordsMatch=!hasPassword || confirmPassword===password;
  let validAddress=false;
  try {
    const url=new URL(address.includes('://') ? address.trim() : `http://${address.trim()}`);
    validAddress=['http:','https:'].includes(url.protocol) && !!url.hostname && !url.username && !url.password && url.pathname==='/' && !url.search && !url.hash;
  } catch { /* Incomplete input. */ }
  useEffect(()=>{const dialog=ref.current;dialog?.showModal();return ()=>{request.current++;dialog?.close();};},[]);
  function changeAddress(value:string) {request.current++;setAddress(value);setResult(null);setTesting(false);setError('');}
  async function testConnection() {
    const id=++request.current;setTesting(true);setResult(null);setError('');
    try {
      const response=await api<Result>('/connections/test','POST',{url:address.trim()});
      if(request.current===id) setResult(response);
    } catch(e) {if(request.current===id) setResult({ok:false,message:e instanceof Error ? translateApiText(e.message) : t('測試失敗', 'Connection test failed')});}
    finally {if(request.current===id) setTesting(false);}
  }
  async function save() {
    setSaving(true);setError('');
    try {
      const payload: Record<string, string> = {name:name.trim(),url:address.trim()};
      if (hasPassword) payload.password = password;
      await api<Node>('/connections','POST',payload);
      await onAdded();
      onClose();
    }
    catch(e) {setError(e instanceof Error ? translateApiText(e.message) : t('新增失敗', 'Failed to add host'));setSaving(false);}
  }
  return <dialog ref={ref} className="gcc-dialog gcc-create-dialog gcc-add-host-dialog" aria-labelledby="add-host-title" onCancel={e=>{if(saving) e.preventDefault();else onClose();}}>
    <div className="gcc-dialog-head"><span><DesktopTower size={18}/>{t('連接運算設備', 'Connect Compute Host')}</span><button className="gcc-icon-button" aria-label={t('關閉新增主機', 'Close add host dialog')} onClick={onClose} disabled={saving}><X size={21}/></button></div>
    <div className="gcc-create-body">
      <h2 id="add-host-title">{t('新增主機', 'Add Host')}</h2><p className="gcc-create-description">{t('輸入主機位址，自動尋找可用的模型服務。', 'Enter a host address to automatically discover reachable model services.')}</p>
      <label className="gcc-field"><span>{t('主機名稱', 'Host Name')}</span><input aria-label={t('主機名稱', 'Host Name')} autoFocus value={name} onChange={e=>setName(e.target.value)} placeholder={t('例如 emily-pc', 'e.g. gpu-node-02')} maxLength={100} disabled={saving} aria-invalid={!!name && (!validName || existing)}/><span className={`gcc-field-help${name && (!validName || existing) ? ' is-error' : ''}`}>{existing ? t('此名稱已被使用。', 'This host name is already in use.') : t('使用英數字、點、底線或連字號。', 'Use letters, digits, dots, underscores, or hyphens.')}</span></label>
      <label className="gcc-field"><span>{t('主機位址', 'Host Address')}</span><input aria-label={t('主機位址', 'Host Address')} value={address} onChange={e=>changeAddress(e.target.value)} placeholder={t('例如 10.8.22.92', 'e.g. 10.8.22.92')} disabled={saving} aria-invalid={!!address && !validAddress}/><span className="gcc-field-help">{t('只需 IP 或主機名稱，不用填連接埠。', 'Enter an IP address or hostname without a port.')}</span></label>
      <label className="gcc-field">
        <span className="gcc-field-label-inline"><LockSimple size={14}/>{t('操作密碼', 'Operation Password')} <small>{t('選填', 'Optional')}</small></span>
        <div className="gcc-password-wrap">
          <input
            type={showPassword ? 'text' : 'password'}
            aria-label={t('操作密碼', 'Operation Password')}
            autoComplete="new-password"
            value={password}
            onChange={e=>{setPassword(e.target.value);if(!e.target.value) setConfirmPassword('');}}
            placeholder={t('留空則不鎖定；設定後載入／卸載模型須驗證', 'Leave blank to skip; protects model load/unload')}
            maxLength={128}
            disabled={saving}
            aria-invalid={hasPassword && !validPassword}
          />
          <button
            type="button"
            className="gcc-icon-button gcc-password-toggle"
            aria-label={showPassword ? t('隱藏密碼', 'Hide password') : t('顯示密碼', 'Show password')}
            onClick={()=>setShowPassword(v=>!v)}
            disabled={saving}
          >{showPassword ? <EyeSlash size={17}/> : <Eye size={17}/>}</button>
        </div>
        <span className={`gcc-field-help${hasPassword && !validPassword ? ' is-error' : ''}`}>
          {hasPassword && !validPassword
            ? t('操作密碼長度需至少 4 個字元，且不可全為空白。', 'Password must be at least 4 non-blank characters.')
            : t('設定後，對此主機執行模型載入、卸載與 GPU 切換前須驗證密碼。', 'When set, loading, unloading, or moving models on this host requires password verification.')}
        </span>
      </label>
      {hasPassword && <label className="gcc-field">
        <span>{t('確認操作密碼', 'Confirm Operation Password')}</span>
        <input
          type={showPassword ? 'text' : 'password'}
          aria-label={t('確認操作密碼', 'Confirm Operation Password')}
          autoComplete="new-password"
          value={confirmPassword}
          onChange={e=>setConfirmPassword(e.target.value)}
          placeholder={t('再次輸入操作密碼', 'Re-enter operation password')}
          maxLength={128}
          disabled={saving}
          aria-invalid={!!confirmPassword && !passwordsMatch}
        />
        {confirmPassword && !passwordsMatch && <span className="gcc-field-help is-error">{t('兩次輸入的密碼不一致。', 'Passwords do not match.')}</span>}
      </label>}
      {result && <div className={`gcc-host-test-result${result.ok ? ' is-success' : ' is-error'}`} role="status">{result.ok ? <Check size={20}/> : <WarningCircle size={20}/>}<div><strong>{result.ok ? t('找到可用服務', 'Services Discovered') : t('連線失敗', 'Connection Failed')}</strong><p>{translateApiText(result.message)}</p>{result.services?.map(service=><p key={service.provider}><strong>{labels[service.provider]}</strong> · {service.provider==='comfyui' ? t('服務在線', 'Service online') : t(`${service.model_count} 個模型`, `${service.model_count} models`)} · {service.latency_ms} ms</p>)}{!result.ok && result.checks?.map(check=><p key={check.provider}>{labels[check.provider]}：{translateApiText(check.reason || '')}</p>)}</div></div>}
      {error && <p className="gcc-field-help is-error" role="alert">{error}</p>}
    </div>
    <div className="gcc-create-actions"><div><button className="gcc-button" onClick={onClose} disabled={saving}>{t('取消', 'Cancel')}</button><button className="gcc-button" disabled={!validAddress || testing || saving} onClick={()=>void testConnection()}>{testing && <CircleNotch size={18} className="gcc-spin"/>}{testing ? t('偵測中', 'Probing…') : t('測試連線', 'Test Connection')}</button><button className="gcc-button gcc-button--primary" disabled={!validAddress || !validName || existing || !validPassword || !passwordsMatch || !result?.ok || testing || saving} onClick={()=>void save()}>{saving && <CircleNotch size={18} className="gcc-spin"/>}{saving ? t('新增中', 'Adding…') : t('新增連線', 'Add Host')}</button></div></div>
  </dialog>;
}
