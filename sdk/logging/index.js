/** Business-authenticated transport only: never embed ctl credentials in clients. */
export function createLogger(options) {
  if (!options.project || !options.environment || !options.transport) throw new Error('project, environment and transport are required');
  const uuid = () => globalThis.crypto.randomUUID();
  const key = `ctl.installation.${options.project}`;
  let storage = options.storage;
  if (!storage) { try { storage = globalThis.localStorage; } catch {} }
  let id;
  try { id = storage?.getItem(key); } catch {}
  if (typeof id !== 'string' || !/^inst_[a-zA-Z0-9-]{16,80}$/.test(id)) {
    id = `inst_${uuid()}`;
    try { storage?.setItem(key, id); } catch {} // Session-only identity if persistence is unavailable.
  }
  const bytes = v => new TextEncoder().encode(v).length;
  const text = (v, max=256) => { if (v==null) return ''; if(typeof v!=='string'||bytes(v)>max||v.includes('\0')) throw new Error('Invalid context field'); return v; };
  function getContext() {
    const native = options.getDeviceInfo?.() || {};
    return { device_id: id, user_id: text(options.getUserId?.()),
      model: text(native.model).trim().replace(/\s+/g, ' '),
      app_version: text(native.appVersion || options.appVersion), source: text(native.platform || options.platform || 'web',80) };
  }
  const attrs = c => [['device',c.device_id],['user',c.user_id],['model',c.model]].filter(([,v])=>v).map(([type,value])=>({type,value}));
  let cache, active = 0;
  async function permission(c, force=false) {
    const fingerprint = JSON.stringify(c);
    if (!force && cache?.key === fingerprint && cache.expires > Date.now()) return cache.value;
    const value = await options.transport.queryPermission({attributes:attrs(c)});
    if (!value || typeof value.allowed !== 'boolean' || !Array.isArray(value.matchedRules) || !Number.isInteger(value.policyVersion)) throw new Error('Invalid permission response');
    if (JSON.stringify(getContext()) !== fingerprint) return {allowed:false,matchedRules:[],policyVersion:value.policyVersion};
    cache = {key:fingerprint,value,expires:Date.now()+Math.max(0,Math.min(options.permissionCacheMs ?? 30000,60000))};
    return value;
  }
  async function shouldReport({force=false}={}) {return permission(getContext(),force);}
  async function report(input) {
    if (active >= 4) return {status:'busy',accepted:0,rejected:0,duplicates:0};
    active++;
    try {
      const c=getContext(), items=Array.isArray(input)?input:[input];
      if (!items.length || items.length>100) throw new Error('A batch must contain 1..100 events');
      const events=items.map(x=>{
        if (!x || typeof x.event!=='string'||!x.event.trim()||bytes(x.event)>160||typeof x.message!=='string'||!x.message.trim()||bytes(x.message)>4096) throw new Error('event and message are required');
        const level=(x.level||'INFO').toUpperCase();if(!['DEBUG','INFO','WARN','ERROR'].includes(level))throw new Error('Invalid level');
        if(x.data!=null&&(typeof x.data!=='object'||Array.isArray(x.data)))throw new Error('data must be an object');
        if(new TextEncoder().encode(JSON.stringify(x.data||{})).length>16384)throw new Error('data exceeds 16 KiB');
        return {schema_version:1,event_id:uuid(),occurred_at:new Date().toISOString(),...c,level,event:x.event,message:x.message,request_id:text(x.request_id),trace_id:text(x.trace_id),data:JSON.parse(JSON.stringify(x.data||{}))};
      });
      if(new TextEncoder().encode(JSON.stringify({events})).length>1024*1024)throw new Error('batch exceeds 1 MiB');
      if (!(await permission(c)).allowed) return {status:'skipped',accepted:0,rejected:0,duplicates:0};
      for(let attempt=0;attempt<3;attempt++) {
        if(JSON.stringify(getContext())!==JSON.stringify(c))return {status:'skipped',accepted:0,rejected:0,duplicates:0};
        try {
          const result=await options.transport.report({events});
          if(!result||!['accepted','rejected','duplicates'].every(k=>Number.isInteger(result[k])&&result[k]>=0)||result.accepted+result.rejected+result.duplicates!==events.length)throw new Error('Invalid upload response');
          if(result.rejected)cache=undefined;
          return {status:result.rejected===events.length?'rejected':'sent',...result};
        } catch(error) {
          cache=undefined;
          if(attempt===2 || (error?.status>=400 && error.status<500 && error.status!==429))throw error;
          await new Promise(resolve=>setTimeout(resolve,Math.max(0,Math.min(options.retryDelayMs??250,5000))*(attempt+1)));
        }
      }
    } finally {active--;}
  }
  return {getContext,shouldReport,report};
}
