import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type MouseEvent } from 'react'
import {
  POINTING_MODES,
  type HeadRange,
  type Lang,
  type Message,
  type Metrics,
  type PointingMode,
  type ShortcutDebug,
} from '../contracts'
import type { VoiceSource } from '../board/speech'
import { gazeConnected, showCursor } from '../facetrack/stores'
import { MAX_STICKY_MARGIN } from '../facetrack/tiles'
import { tracker } from '../facetrack/tracker'
import { useTrackerStatus } from '../facetrack/useTrackerStatus'
import { StatusDot } from '../lib/StatusDot'
import { useSocket } from '../lib/useSocket'
import { CameraPreview } from './CameraPreview'

/**
 * Keyboard stand-in for the headband (PRD D15, P0). Sends the same CLENCH / DOUBLE_BLINK /
 * LONG_CLENCH events the Sensor Service will send, on /ws/input, so the Core cannot tell them apart.
 *
 *   Space            CLENCH (strength 1.0)
 *   hold Space       LONG_CLENCH instead of CLENCH, sent the moment `long_clench_ms` is reached
 *                    (from the Core's SETTINGS, the profile's 2.5 s by default)
 *   B                DOUBLE_BLINK
 *   `                expand / collapse this panel
 *
 * Collapsed (the default) it is a small pill in the bottom-left corner, below the tile grid, so it
 * never covers a tile. The keys work whether it is expanded or not.
 *
 * The controls show what the Core says (SETTINGS arrives on connect and after every change), so a
 * change from the console or another tab shows up here too.
 *
 * After every confirmed message the Core sends METRICS: the panel shows what it took against Day 1
 * mode ("Took 2 clenches, 0 s waiting (Day 1: 5 clenches, 6 s)"), and the collapsed pill keeps a
 * short "2 vs 5 clenches" so the demo can show it without opening the panel.
 *
 * Pointing (PRD D2): the pointing mode selector (Auto / Scan / Webcam / Gaze / Head tilt), a small camera
 * preview with the head's yaw and pitch, "Calibrate head range" and the cursor dot toggle.
 */

const DEFAULT_LONG_CLENCH_MS = 2500 // until the Core's SETTINGS says otherwise
const MAX_LOG = 10
const MODE_LABEL: Record<PointingMode, string> = {
  auto: 'Auto',
  scan: 'Scan',
  webcam: 'Webcam',
  gaze: 'Gaze',
  headtilt: 'Head tilt',
}

interface LogEntry {
  id: number
  time: string
  text: string
  sent: boolean
}

const now = () => Date.now() / 1000

function describe(msg: Message): string {
  switch (msg.type) {
    case 'LONG_CLENCH':
      return `LONG_CLENCH ${msg.duration.toFixed(1)} s`
    case 'SETTINGS': {
      const picks = msg.speak_picks === undefined ? '' : ` speak picks ${msg.speak_picks ? 'on' : 'off'}`
      const learning = msg.learning === undefined ? '' : msg.learning ? ' learning' : ' day 1'
      return `SETTINGS ${msg.pointing_mode} ${msg.scan_ms} ms${msg.lang ? ` ${msg.lang}` : ''}${picks}${learning}`
    }
    default:
      return msg.type
  }
}

/** Scan steps as waiting time: "0 s", "4 s", "2.5 s". */
function seconds(steps: number, scanMs: number): string {
  const s = (steps * scanMs) / 1000
  return `${Number.isInteger(s) ? s : s.toFixed(1)} s`
}

/** SHORTCUT_DEBUG as one line: top phrase, history share, Jev, and the decision with its reason. */
function shortcutLine(d: ShortcutDebug): string {
  const quote = (t: string) => `"${t.length > 40 ? `${t.slice(0, 39)}…` : t}"`
  const jev =
    d.jev === 'off'
      ? 'Jev off'
      : d.jev === 'waiting' || d.jev_pick === null
        ? 'Jev: no answer yet'
        : `Jev: ${quote(d.jev_pick)} ${(d.jev_confidence ?? 0).toFixed(2)}`
  const top = d.top === null ? 'no top phrase' : `${quote(d.top)} (history ${d.history_share.toFixed(2)})`
  return `${top} · ${jev} · shortcut: ${d.shortcut ? 'yes' : 'no'} (${d.reason})`
}

function clenches(n: number): string {
  return `${n} clench${n === 1 ? '' : 'es'}`
}

interface Props {
  /** where the last thing the board said came from; null = nothing said yet */
  voiceSource: VoiceSource | null
  /** the saved head range; null = not calibrated, the defaults are in use */
  headRange: HeadRange | null
  /** the board wants the camera on (Webcam or Auto mode) */
  cameraWanted: boolean
  /** open the head-range calibration overlay on the board */
  onCalibrate: () => void
}

export default function DevPanel({ voiceSource, headRange, cameraWanted, onCalibrate }: Props) {
  const [open, setOpen] = useState(false)
  // null until the Core's first SETTINGS arrives: nothing is assumed.
  const [pointingMode, setPointingMode] = useState<PointingMode | null>(null)
  const [scanMs, setScanMs] = useState<number | null>(null)
  const [lang, setLang] = useState<Lang | null>(null)
  const [speakPicks, setSpeakPicks] = useState<boolean | null>(null)
  const [learning, setLearning] = useState<boolean | null>(null)
  const [longClenchMs, setLongClenchMs] = useState(DEFAULT_LONG_CLENCH_MS)
  const [margin, setMargin] = useState<number | null>(null) // tile_switch_margin, 0..0.2
  const [metrics, setMetrics] = useState<Metrics | null>(null)
  const [shortcut, setShortcut] = useState<ShortcutDebug | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])
  const nextId = useRef(0)
  const scanTimer = useRef<number | undefined>(undefined)
  const sliding = useRef(false)
  const marginTimer = useRef<number | undefined>(undefined)
  const marginSliding = useRef(false)
  const { status, send } = useSocket('/ws/input', {
    onMessage: (msg) => {
      if (msg.type === 'METRICS') {
        setMetrics(msg)
        return
      }
      if (msg.type === 'SHORTCUT_DEBUG') {
        setShortcut(msg)
        return
      }
      if (msg.type !== 'SETTINGS') return
      setPointingMode(msg.pointing_mode)
      if (!sliding.current) setScanMs(msg.scan_ms)
      if (msg.lang) setLang(msg.lang)
      if (msg.speak_picks !== undefined) setSpeakPicks(msg.speak_picks)
      if (msg.learning !== undefined) setLearning(msg.learning)
      if (msg.long_clench_ms !== undefined) setLongClenchMs(msg.long_clench_ms)
      if (msg.tile_switch_margin !== undefined && !marginSliding.current) setMargin(msg.tile_switch_margin)
    },
  })
  const known = pointingMode !== null && scanMs !== null
  const camera = useTrackerStatus()
  const cursorDot = useSyncExternalStore(showCursor.subscribe, showCursor.get)
  const eyeTracker = useSyncExternalStore(gazeConnected.subscribe, gazeConnected.get)

  const emit = useCallback(
    (msg: Message) => {
      const sent = send(msg)
      const time = new Date().toLocaleTimeString([], { hour12: false }) + '.' + String(Date.now() % 1000).padStart(3, '0')
      setLog((l) => [{ id: nextId.current++, time, text: describe(msg), sent }, ...l].slice(0, MAX_LOG))
    },
    [send],
  )

  const clench = useCallback(() => emit({ type: 'CLENCH', t: now(), strength: 1.0 }), [emit])
  const doubleBlink = useCallback(() => emit({ type: 'DOUBLE_BLINK', t: now() }), [emit])
  const reset = () => emit({ type: 'RESET' })
  const longClench = useCallback(
    () => emit({ type: 'LONG_CLENCH', t: now(), duration: longClenchMs / 1000 }),
    [emit, longClenchMs],
  )

  // Keyboard: Space / B / backtick.
  useEffect(() => {
    let longTimer: number | undefined
    let spaceHeld = false
    let longSent = false

    const releaseSpace = (sendClench: boolean) => {
      window.clearTimeout(longTimer)
      if (spaceHeld && !longSent && sendClench) clench()
      spaceHeld = false
      longSent = false
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.ctrlKey || e.metaKey || e.altKey) return
      if (e.code === 'Space') {
        e.preventDefault() // no page scroll, no button "click" on a focused control
        if (e.repeat || spaceHeld) return
        spaceHeld = true
        longTimer = window.setTimeout(() => {
          longSent = true
          longClench()
        }, longClenchMs)
      } else if (e.code === 'KeyB') {
        if (!e.repeat) doubleBlink()
      } else if (e.code === 'Backquote') {
        if (!e.repeat) setOpen((v) => !v)
      }
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (e.code !== 'Space') return
      e.preventDefault()
      releaseSpace(true)
    }
    // Focus lost while Space is down: no keyup will come, so drop the press instead of guessing.
    const onBlur = () => releaseSpace(false)

    window.addEventListener('keydown', onKeyDown, true)
    window.addEventListener('keyup', onKeyUp, true)
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKeyDown, true)
      window.removeEventListener('keyup', onKeyUp, true)
      window.removeEventListener('blur', onBlur)
      window.clearTimeout(longTimer)
    }
  }, [clench, doubleBlink, longClench, longClenchMs])

  // Every change keeps the other values as the Core last reported them. The Core answers with
  // SETTINGS, which is what the controls then show.
  const onScanChange = (ms: number) => {
    if (pointingMode === null) return
    setScanMs(ms)
    sliding.current = true
    // Send once the slider settles instead of on every step.
    window.clearTimeout(scanTimer.current)
    scanTimer.current = window.setTimeout(() => {
      sliding.current = false
      emit({ type: 'SETTINGS', pointing_mode: pointingMode, scan_ms: ms })
    }, 250)
  }

  // Webcam / gaze sticky edges, live: the board takes the value from the Core's SETTINGS.
  const onMarginChange = (percent: number) => {
    if (!known) return
    const value = percent / 100
    setMargin(value)
    marginSliding.current = true
    window.clearTimeout(marginTimer.current)
    marginTimer.current = window.setTimeout(() => {
      marginSliding.current = false
      emit({ type: 'SETTINGS', pointing_mode: pointingMode, scan_ms: scanMs, tile_switch_margin: value })
    }, 250)
  }

  const toggleLang = () => {
    if (!known || lang === null) return
    emit({ type: 'SETTINGS', pointing_mode: pointingMode, scan_ms: scanMs, lang: lang === 'en' ? 'es' : 'en' })
  }

  const toggleSpeakPicks = () => {
    if (!known || speakPicks === null) return
    emit({ type: 'SETTINGS', pointing_mode: pointingMode, scan_ms: scanMs, speak_picks: !speakPicks })
  }

  const setMode = (mode: PointingMode) => {
    if (!known || mode === pointingMode) return
    emit({ type: 'SETTINGS', pointing_mode: mode, scan_ms: scanMs })
  }

  // Day 1 mode = learning off: menu.yaml order, the fixed Suggested list, no shortcut.
  const toggleDay1 = () => {
    if (!known || learning === null) return
    emit({ type: 'SETTINGS', pointing_mode: pointingMode, scan_ms: scanMs, learning: !learning })
  }

  const took =
    metrics &&
    `Took ${clenches(metrics.selections)}, ${seconds(metrics.scan_steps, scanMs ?? 1000)} waiting ` +
      `(Day 1: ${clenches(metrics.day1_selections)}, ${seconds(metrics.day1_scan_steps, scanMs ?? 1000)})`

  // Buttons never take focus, so Space always means "clench", never "press the focused button".
  const noFocus = (e: MouseEvent) => e.preventDefault()
  const btn = 'rounded-md bg-zinc-700 px-2 py-1.5 font-semibold hover:bg-zinc-600 active:bg-zinc-500'

  if (!open) {
    return (
      <button
        type="button"
        onMouseDown={noFocus}
        onClick={() => setOpen(true)}
        title="Dev input: click or press ` to expand"
        className="fixed bottom-1.5 left-2 z-50 flex items-center gap-1.5 rounded-full bg-zinc-800/90 px-2.5 py-0.5 text-xs text-zinc-300 ring-1 ring-zinc-600 hover:bg-zinc-700"
      >
        <StatusDot status={status} label="Input" /> Dev
        {learning === false && <span className="font-semibold text-amber-300">Day 1</span>}
        {metrics && (
          <span className="text-zinc-400" title={took ?? undefined}>
            · {metrics.selections} vs {metrics.day1_selections} clenches
          </span>
        )}
        <span className="text-zinc-500">`</span>
      </button>
    )
  }

  return (
    <aside className="fixed bottom-2 left-2 z-50 w-80 rounded-xl bg-zinc-800/95 p-4 text-sm text-zinc-100 shadow-2xl ring-1 ring-zinc-600">
      <header className="mb-3 flex items-center justify-between">
        <span className="flex items-center gap-2 font-bold">
          <StatusDot status={status} label="Input" /> Dev input
        </span>
        <button
          type="button"
          onMouseDown={noFocus}
          onClick={() => setOpen(false)}
          className="rounded px-1.5 text-xs text-zinc-400 hover:bg-zinc-700 hover:text-zinc-200"
        >
          collapse `
        </button>
      </header>

      <p className="mb-3 text-xs text-zinc-400">
        Space = clench · hold Space {longClenchMs / 1000} s = long clench · B = double blink
      </p>

      <div className="mb-3 grid grid-cols-3 gap-2">
        <button type="button" className={btn} onMouseDown={noFocus} onClick={clench}>
          Clench
        </button>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={doubleBlink}>
          Dbl blink
        </button>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={longClench}>
          Long
        </button>
      </div>

      <button
        type="button"
        className={`${btn} mb-3 w-full text-xs`}
        onMouseDown={noFocus}
        onClick={reset}
        title="RESET: back to Home with the highlight on the first tile"
      >
        Reset to Home
      </button>

      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs text-zinc-400">Pointing</span>
        <span className="flex gap-1">
          {POINTING_MODES.map((m) => (
            <button
              key={m}
              type="button"
              onMouseDown={noFocus}
              onClick={() => setMode(m)}
              disabled={!known}
              className={`rounded-md px-1.5 py-1 text-xs font-semibold ${
                m === pointingMode ? 'bg-yellow-300 text-zinc-900' : 'bg-zinc-700 text-zinc-300 hover:bg-zinc-600'
              }`}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </span>
      </div>

      {(cameraWanted || camera.kind !== 'off') && <CameraPreview onRetry={() => void tracker.start()} />}

      {(pointingMode === 'gaze' || pointingMode === 'auto') && (
        <p className="mb-3 text-xs text-zinc-400" title="docs/eye-tracking.md: feed window.clenchGaze">
          Eye tracker:{' '}
          <span className={eyeTracker ? 'text-emerald-300' : 'text-zinc-200'}>
            {eyeTracker ? 'connected (gaze slot)' : 'not connected'}
          </span>
        </p>
      )}

      <div className="mb-3 flex items-center justify-between gap-2">
        <button
          type="button"
          className={`${btn} text-xs disabled:opacity-40`}
          onMouseDown={noFocus}
          onClick={onCalibrate}
          disabled={camera.kind !== 'on'}
          title={camera.kind === 'on' ? 'Center, left, right, up, down: about 8 s' : 'Needs the camera: Auto or Webcam mode'}
        >
          Calibrate head range
        </button>
        <span className="text-xs text-zinc-400">{headRange ? 'calibrated' : 'defaults'}</span>
      </div>

      <label className="mb-3 block" title="How far the head's point must be inside a new tile before the highlight moves">
        <span className="flex justify-between text-xs text-zinc-400">
          <span>Tile switch margin</span>
          <span>{margin === null ? 'waiting for Core' : `${Math.round(margin * 100)}%`}</span>
        </span>
        <input
          type="range"
          min={0}
          max={MAX_STICKY_MARGIN * 100}
          step={1}
          disabled={!known || margin === null}
          value={Math.round((margin ?? 0.05) * 100)}
          onChange={(e) => onMarginChange(Number(e.target.value))}
          onPointerUp={(e) => e.currentTarget.blur()}
          className="w-full accent-yellow-300"
        />
      </label>

      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-zinc-400">Cursor dot</span>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={() => showCursor.set(!cursorDot)}>
          <span className={cursorDot ? 'text-yellow-300' : 'text-zinc-400'}>On</span>
          {' / '}
          <span className={cursorDot ? 'text-zinc-400' : 'text-yellow-300'}>Off</span>
        </button>
      </div>

      <label className="mb-3 block">
        <span className="flex justify-between text-xs text-zinc-400">
          <span>Scan speed{pointingMode && pointingMode !== 'auto' ? ` (${pointingMode})` : ''}</span>
          <span>{scanMs === null ? 'waiting for Core' : `${scanMs} ms / tile`}</span>
        </span>
        <input
          type="range"
          min={300}
          max={3000}
          step={100}
          disabled={!known}
          value={scanMs ?? 1000}
          onChange={(e) => onScanChange(Number(e.target.value))}
          onPointerUp={(e) => e.currentTarget.blur()}
          className="w-full accent-yellow-300"
        />
      </label>

      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-zinc-400">Language</span>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={toggleLang}>
          <span className={lang === 'en' ? 'text-yellow-300' : 'text-zinc-400'}>EN</span>
          {' / '}
          <span className={lang === 'es' ? 'text-yellow-300' : 'text-zinc-400'}>ES</span>
        </button>
      </div>

      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-zinc-400">Speak picks</span>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={toggleSpeakPicks}>
          <span className={speakPicks ? 'text-yellow-300' : 'text-zinc-400'}>On</span>
          {' / '}
          <span className={speakPicks === false ? 'text-yellow-300' : 'text-zinc-400'}>Off</span>
        </button>
      </div>

      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs text-zinc-400" title="Learning off: menu.yaml order, fixed Suggested list, no shortcut">
          Day 1 mode
        </span>
        <button type="button" className={btn} onMouseDown={noFocus} onClick={toggleDay1}>
          <span className={learning === false ? 'text-amber-300' : 'text-zinc-400'}>On</span>
          {' / '}
          <span className={learning ? 'text-yellow-300' : 'text-zinc-400'}>Off</span>
        </button>
      </div>

      <p className="mb-3 text-xs text-zinc-400">
        Voice: <span className="text-zinc-200">{voiceSource ?? 'nothing said yet'}</span>
      </p>

      <p className="mb-3 text-xs text-zinc-400">
        Last message:{' '}
        <span className="text-zinc-200">{took ?? 'nothing sent yet'}</span>
      </p>

      <p className="mb-3 text-xs text-zinc-400" title="SHORTCUT_DEBUG: sent after every Home render">
        Shortcut:{' '}
        <span className={shortcut?.shortcut ? 'text-emerald-300' : 'text-zinc-200'}>
          {shortcut ? shortcutLine(shortcut) : 'waiting for Home'}
        </span>
      </p>

      <ol className="space-y-0.5 font-mono text-xs">
        {log.length === 0 && <li className="text-zinc-500">No events sent yet</li>}
        {log.map((entry) => (
          <li key={entry.id} className={entry.sent ? 'text-zinc-300' : 'text-red-400'}>
            <span className="text-zinc-500">{entry.time}</span> {entry.text}
            {!entry.sent && ' (not sent: offline)'}
          </li>
        ))}
      </ol>
    </aside>
  )
}
