import {describe,it,expect} from 'vitest'
import {draftRows,changes,editRow} from './environmentForm'
describe('secret draft protocol',()=>{
 it('preserves secret without sending placeholders',()=>{
  const rows=draftRows([{key:'PASSWORD',secret:true,configured:true}])
  expect(changes(rows)).toEqual([{key:'PASSWORD',operation:'keep',secret:true}])
 })
 it('distinguishes empty string and removal',()=>{
  let rows=draftRows([{key:'PASSWORD',secret:true,configured:true}])
  rows=editRow(rows,0,{operation:'set',value:''})
  expect(changes(rows)[0]).toEqual({key:'PASSWORD',operation:'set',secret:true,value:''})
  rows=editRow(rows,0,{operation:'remove'})
  expect(changes(rows)).toEqual([{key:'PASSWORD',operation:'remove'}])
 })
 it('keeps existing nonsecret values and rejects duplicate keys',()=>{
  const rows=draftRows([{key:'TEXT',secret:false,configured:true,value:' $ = '}])
  expect(changes(rows)[0].operation).toBe('keep')
  expect(()=>changes([...rows,...rows])).toThrow('重复')
 })
})
