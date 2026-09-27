import { describe, expect, it } from 'vitest'
import type { ComputerState } from '../contracts'
import { ComputerPointer } from './computerPointer'
import { HEAD_TUNING } from './tilePointer'
import type { PointSample } from './source'

const state: ComputerState = { type:'COMPUTER_STATE', active:true, seq:10, paused:false, pointer:'gaze', highlight:0,
  tiles:[{id:'a',label:'A',left:0,top:0,right:.45,bottom:1},{id:'b',label:'B',left:.55,top:0,right:1,bottom:1}] }
const sample = (t:number, source:'head'|'gaze'='head'): PointSample => ({source,t,found:true,confidence:1,point:{x:.8,y:.5}})

describe('Chromium pointing relay',()=>{
  it('uses remote rectangles and sends a heartbeat without clicks',()=>{
    const p=new ComputerPointer(HEAD_TUNING)
    expect(p.update(sample(0),state,HEAD_TUNING,'tracking')).toMatchObject({type:'COMPUTER_POINT',tile:1,seq:10,source:'webcam'})
    expect(p.update(sample(100),state,HEAD_TUNING,'tracking')).toBeNull()
    expect(p.update(sample(210),state,HEAD_TUNING,'tracking')?.tile).toBe(1)
  })
  it('keeps gaze hold and resets choices on a new layout',()=>{
    const tuning={...HEAD_TUNING,holdMs:300}
    const p=new ComputerPointer(tuning)
    expect(p.update(sample(0,'gaze'),state,tuning,'tracking')?.tile).toBe(0)
    expect(p.update(sample(310,'gaze'),state,tuning,'tracking')?.tile).toBe(1)
    expect(p.update(sample(320,'gaze'),{...state,seq:11},tuning,'tracking')?.tile).toBe(0)
  })
  it('reports loss and paused layouts without pointing, and stops on exit',()=>{
    const p=new ComputerPointer(HEAD_TUNING)
    expect(p.update(sample(0),{...state,paused:true},HEAD_TUNING,'tracking')?.tile).toBeNull()
    expect(p.update({...sample(300,'gaze'),found:false,point:null},state,HEAD_TUNING,'no_tracker'))
      .toMatchObject({tile:null,found:false,status:'no_tracker'})
    expect(p.update(sample(600),{...state,active:false},HEAD_TUNING,'tracking')).toBeNull()
  })
})
