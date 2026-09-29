import { useEffect, useRef } from 'react'
import type { ComputerState, HeadRange, PointingMode } from '../contracts'
import type { Send } from '../lib/useSocket'
import { ComputerPointer } from './computerPointer'
import { gaze } from './gaze'
import { gazePointerTuning } from './gazeTuning'
import { gazeOwnsCamera } from './cameraOwner'
import { chooseSource, fromHead, type PointSample } from './source'
import { gazeTuning, showCursor } from './stores'
import { DwellTimer, HEAD_TUNING } from './tilePointer'
import { tracker } from './tracker'

interface Options {
  state: ComputerState | null
  mode: PointingMode | null
  range: HeadRange
  margin: number
  connected: boolean
  paused: boolean
  send: Send
  pick: (seq: number, tile: number) => boolean
  savedRange: HeadRange | null
  voiceSource: string | null
}

export function useComputerPointing(options: Options) {
  const latest = useRef(options)
  useEffect(() => { latest.current = options })
  const active = options.connected && !!options.state?.active
  useEffect(() => {
    tracker.setBackground(active)
    if (!active) return
    const pointer = new ComputerPointer(HEAD_TUNING)
    const dwell = new DwellTimer()
    let lastSeq = -1, progress = 0
    const sample = (s: PointSample) => {
      const { state, mode, margin, send, paused } = latest.current
      if (!state?.active || s.source !== chooseSource(mode, gaze.available(s.t), !gazeOwnsCamera())) return
      const tuning = s.source === 'gaze' ? gazePointerTuning(gazeTuning.get(), margin) : { ...HEAD_TUNING, margin }
      const status = s.found ? 'tracking' : s.source === 'gaze' ? (gaze.connected(s.t) ? 'lost' : 'no_tracker') :
        tracker.status.kind === 'error' ? 'camera_error' : tracker.status.kind === 'starting' ? 'starting' : 'lost'
      const msg = pointer.update(s, paused ? { ...state, paused: true } : state, tuning, status)
      if (msg) send(msg)
      if (state.seq !== lastSeq) { dwell.reset(); lastSeq = state.seq }
      const prefs = gazeTuning.get()
      dwell.dwellMs = prefs.dwellMs
      const d = dwell.update(prefs.dwell && s.source === 'gaze' && s.found && !paused && !state.paused ? pointer.tile : null, s.t)
      progress = d.progress
      if (d.fire && pointer.tile !== null) latest.current.pick(state.seq, pointer.tile)
    }
    const head = tracker.subscribeSample(s => sample(fromHead(s, latest.current.range)))
    const eyes = gaze.subscribe(sample)
    const watch = window.setInterval(() => {
      const now = performance.now()
      const source = chooseSource(latest.current.mode, gaze.available(now), !gazeOwnsCamera())
      if (!source) { pointer.point = null; progress = 0; dwell.reset() }
      if (source === 'gaze' && !gaze.connected(now)) sample({ source, t: now, point: null, found: false, confidence: 0 })
      if (source === 'head' && now - tracker.sample.t > 500) sample({ source, t: now, point: null, found: false, confidence: 0 })
      const { send, savedRange, voiceSource } = latest.current
      const angles = tracker.sample.raw
      send({ type: 'COMPUTER_TELEMETRY', x: pointer.point?.x ?? null, y: pointer.point?.y ?? null,
        show_cursor: showCursor.get(), dwell: gazeTuning.get().dwell, progress,
        camera: tracker.status.kind, yaw: angles?.yaw ?? null, pitch: angles?.pitch ?? null,
        eye_connected: gaze.connected(now), head_range: savedRange, voice_source: voiceSource })
    }, 100)
    return () => { head(); eyes(); window.clearInterval(watch); tracker.setBackground(false) }
  }, [active])
}
