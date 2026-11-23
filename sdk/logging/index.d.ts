export interface Attribute { type:'device'|'user'|'model'; value:string }
export interface Decision { allowed:boolean; matchedRules:{ruleId:string;matchedBy:'specified'|'percentage'}[];policyVersion:number }
export interface LogInput { level?:'debug'|'info'|'warn'|'error'|'DEBUG'|'INFO'|'WARN'|'ERROR';event:string;message:string;request_id?:string;trace_id?:string;data?:Record<string,unknown> }
export interface Context {device_id:string;user_id:string;model:string;app_version:string;source:string}
export interface Event extends Context {schema_version:1;event_id:string;occurred_at:string;level:string;event:string;message:string;request_id:string;trace_id:string;data:Record<string,unknown>}
export interface Counts {accepted:number;rejected:number;duplicates:number}
export interface Options {
 project:string;environment:string;platform?:string;appVersion?:string;
 storage?:{getItem(key:string):string|null|undefined;setItem(key:string,value:string):void};
 getUserId?:()=>string|undefined;getDeviceInfo?:()=>{model?:string;appVersion?:string;platform?:string};
 permissionCacheMs?:number;retryDelayMs?:number;
 transport:{queryPermission(input:{attributes:Attribute[]}):Promise<Decision>;report(input:{events:Event[]}):Promise<Counts>};
}
export function createLogger(options:Options):{getContext():Context;shouldReport(options?:{force?:boolean}):Promise<Decision>;report(input:LogInput|LogInput[]):Promise<Counts&{status:'busy'|'skipped'|'rejected'|'sent'}>};
