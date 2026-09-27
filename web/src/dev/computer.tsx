// The same DevPanel and calibration components as Home, mounted in Chromium's isolated world.
import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import type { ComputerTelemetry, HeadRange, Message, Settings } from '../contracts'
import DevPanel from './DevPanel'
import { CalibrationOverlay } from '../facetrack/CalibrationOverlay'
import { CursorDot, GazeNotice } from '../facetrack/indicators'
import { cursor, gazeConnected, gazeTuning, showCursor } from '../facetrack/stores'
import { tracker } from '../facetrack/tracker'
import type { VoiceSource } from '../board/speech'
import '../index.css'

type State = { settings?: Settings; devOpen?: boolean; help?: number | null; telemetry?: ComputerTelemetry | null }
declare global {
  interface Window {
    clenchTrustedInput: (payload: string) => void
    clenchControls: (state: State) => void
    clenchControlsCSS: string
    clenchCalibrating: boolean
  }
}
const listeners = new Set<(msg: Message) => void>()
let current: State = {}, syncing = false
const control = (action: 'cursor' | 'dwell' | 'retry' | 'calibrate' | 'calibration_done', value = false) =>
  window.clenchTrustedInput(JSON.stringify({ event: 'CONTROL', control: { type: 'COMPUTER_CONTROL', action, value } }))
window.clenchTransport = {
  send: msg => {
    window.clenchTrustedInput(JSON.stringify(msg.type === 'SETTINGS' ? { event: 'SETTINGS', patch: msg } : { event: msg.type }))
    return true
  },
  subscribe: receive => {
    listeners.add(receive)
    if (current.settings) receive(current.settings)
    return () => { listeners.delete(receive) }
  },
}
showCursor.subscribe(() => { if (!syncing) control('cursor', showCursor.get()) })
gazeTuning.subscribe(() => { if (!syncing) control('dwell', gazeTuning.get().dwell) })
// Retry remains the existing CameraPreview action, forwarded to the board's camera owner.
tracker.start = async () => { control('retry') }

export function Controls() {
  const [state, setState] = useState(current)
  const [calibrating, setCalibrating] = useState(false)
  useEffect(() => {
    window.clenchControls = next => {
      current = next
      if (next.settings) listeners.forEach(fn => fn(next.settings!))
      const t = next.telemetry
      if (t) {
        syncing = true
        showCursor.set(t.show_cursor)
        gazeTuning.set({ ...gazeTuning.get(), dwell: t.dwell })
        gazeConnected.set(t.eye_connected)
        cursor.set(t.x !== null && t.y !== null ? { x: t.x, y: t.y } : null)
        tracker.mirror(t.camera === 'on' ? { kind: 'on', delegate: 'CPU' } : t.camera === 'error' ?
          { kind: 'error', problem: 'none', detail: 'Check the board camera permission' } : { kind: t.camera },
          t.yaw !== null && t.pitch !== null ? { yaw: t.yaw, pitch: t.pitch } : null)
        syncing = false
      }
      setState(next)
      if (next.help != null && window.clenchCalibrating) {
        window.clenchCalibrating = false
        setCalibrating(false)
        control('calibration_done')
      }
    }
  }, [])
  const closeCalibration = () => { window.clenchCalibrating = false; setCalibrating(false); control('calibration_done') }
  const save = async (range: HeadRange) => {
    // Uses the board's existing API client; an acknowledgement is required before showing saved.
    window.clenchTrustedInput(JSON.stringify({ event: 'CONTROL', control: { type: 'COMPUTER_CONTROL', action: 'head_range', head_range: range } }))
    for (let i = 0; i < 50; i++) {
      await new Promise(resolve => setTimeout(resolve, 100))
      if (JSON.stringify(current.telemetry?.head_range) === JSON.stringify(range)) return range
    }
    throw new Error('Board did not save head range')
  }
  return <>
    <CursorDot />
    <div className="pointer-events-none fixed right-3 top-3"><GazeNotice lang={state.settings?.lang ?? 'en'} mode={state.settings?.pointing_mode ?? null} /></div>
    {state.help == null && !calibrating && <DevPanel voiceSource={state.telemetry?.voice_source as VoiceSource | null} headRange={state.telemetry?.head_range ?? null}
      cameraWanted={state.telemetry?.camera === 'on'} keyboard={false} controlledOpen={!!state.devOpen}
      onOpenChange={() => window.clenchTrustedInput(JSON.stringify({ event: 'DEV_TOGGLE' }))}
      onCalibrate={() => { window.clenchCalibrating = true; setCalibrating(true); control('calibrate') }} />}
    {calibrating && state.help == null && <CalibrationOverlay lang={state.settings?.lang ?? 'en'} save={save}
      onSaved={() => {}} onClose={closeCalibration} />}
  </>
}
function mount() {
  const host = document.createElement('div')
  host.id = 'clench-dev'
  Object.assign(host.style, { position: 'fixed', inset: '0', zIndex: '2147483647', pointerEvents: 'none' })
  const shadow = host.attachShadow({ mode: 'closed' })
  const style = document.createElement('style')
  style.textContent = window.clenchControlsCSS + '\n:host{all:initial;font:16px ui-sans-serif,system-ui;color-scheme:dark}aside,button,input{pointer-events:auto}'
  const root = document.createElement('div')
  shadow.append(style, root)
  document.documentElement.append(host)
  createRoot(root).render(<Controls />)
}
window.clenchControls = state => { current = state }
if (window.top === window) {
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, { once: true })
  else mount()
}
