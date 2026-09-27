import { useEffect, useRef } from 'react'
import type { ComputerState, HeadRange, PointingMode } from '../contracts'
import type { Send } from '../lib/useSocket'
import { ComputerPointer } from './computerPointer'
import { gaze } from './gaze'
import { gazePointerTuning } from './gazeTuning'
import { nativeGazeActive } from './native'
import { chooseSource, fromHead, type PointSample } from './source'
import { gazeTuning } from './stores'
import { HEAD_TUNING } from './tilePointer'
import { tracker } from './tracker'

interface Options {
  state: ComputerState | null
  mode: PointingMode | null
  range: HeadRange
  margin: number
  connected: boolean
  paused: boolean
  send: Send
}

export function useComputerPointing(options: Options) {
  const latest = useRef(options)
  useEffect(() => { latest.current = options })
  const active = options.connected && !!options.state?.active
  useEffect(() => {
    tracker.setBackground(active)
    if (!active) return
    const pointer = new ComputerPointer(HEAD_TUNING)
    const sample = (s: PointSample) => {
      const { state, mode, margin, send, paused } = latest.current
      if (!state?.active || s.source !== chooseSource(mode, gaze.available(s.t), !nativeGazeActive())) return
      const tuning = s.source === 'gaze' ? gazePointerTuning(gazeTuning.get(), margin) : { ...HEAD_TUNING, margin }
      const status = s.found ? 'tracking' : s.source === 'gaze' ? (gaze.connected(s.t) ? 'lost' : 'no_tracker') :
        tracker.status.kind === 'error' ? 'camera_error' : tracker.status.kind === 'starting' ? 'starting' : 'lost'
      const msg = pointer.update(s, paused ? { ...state, paused: true } : state, tuning, status)
      if (msg) send(msg)
    }
    const head = tracker.subscribeSample(s => sample(fromHead(s, latest.current.range)))
    const eyes = gaze.subscribe(sample)
    const watch = window.setInterval(() => {
      const now = performance.now()
      const source = chooseSource(latest.current.mode, gaze.available(now), !nativeGazeActive())
      if (source === 'gaze' && !gaze.connected(now)) sample({ source, t: now, point: null, found: false, confidence: 0 })
      if (source === 'head' && now - tracker.sample.t > 500) sample({ source, t: now, point: null, found: false, confidence: 0 })
    }, 200)
    return () => { head(); eyes(); window.clearInterval(watch); tracker.setBackground(false) }
  }, [active])
}
