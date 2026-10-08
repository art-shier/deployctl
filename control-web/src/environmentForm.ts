import type {MaskedVariable} from './api'
export interface DraftRow{key:string;original?:string;value:string;secret:boolean;operation:'keep'|'set'|'remove'}
export function draftRows(values:MaskedVariable[]):DraftRow[]{return values.map(v=>({key:v.key,original:v.key,value:v.value??'',secret:v.secret,operation:'keep'}))}
export function editRow(rows:DraftRow[],index:number,update:Partial<DraftRow>):DraftRow[]{return rows.map((r,i)=>i===index?{...r,...update}:r)}
export function changes(rows:DraftRow[]){
 const seen=new Set<string>()
 return rows.map(row=>{
  if(!/^[A-Z_][A-Z0-9_]{0,127}$/.test(row.key))throw new Error('变量名必须为大写字母、数字或下划线。')
  if(seen.has(row.key))throw new Error('变量名重复，请检查。')
  seen.add(row.key)
  if(row.operation==='remove')return {key:row.key,operation:'remove'}
  if(row.value.length>4096)throw new Error('变量值不能超过 4096 个字符。')
  if(row.operation==='keep')return {key:row.key,operation:'keep',secret:row.secret}
  return {key:row.key,operation:'set',secret:row.secret,value:row.value}
 })
}
