import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react'
import type { Lang, Message } from '../contracts'
import type { VoiceSource } from '../board/speech'
import { StatusDot } from '../lib/StatusDot'
import { useSocket } from '../lib/useSocket'

/**
 * Keyboard stand-in for the headband (PRD D15, P0). Sends the same CLENCH / DOUBLE_BLINK /
 * LONG_CLENCH events the Sensor Service will send, on /ws/input, so the Core cannot tell them apart.
 *
 *   Space            CLENCH (strength 1.0)
 *   hold Space 1.5 s LONG_CLENCH instead of CLENCH (sent the moment 1.5 s is reached)
 *   B                DOUBLE_BLINK
 *   `                expand / collapse this panel
 *
 * Collapsed (the default) it is a small pill in the bottom-left corner, below the tile grid, so it
 * never covers a tile. The keys work whether it is expanded or not.
 */

const LONG_CLENCH_S = 1.5
const MAX_LOG = 10

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
      return `SETTINGS ${msg.scan_ms} ms${msg.lang ? ` ${msg.lang}` : ''}${picks}`
    }
    default:
      return msg.type
  }
}

interface Props {
  lang: Lang
  /** where the last thing the board said came from; null = nothing said yet */
  voiceSource: VoiceSource | null
}

export default function DevPanel({ lang, voiceSource }: Props) {
  const [open, setOpen] = useState(false)
  const [scanMs, setScanMs] = useState(1000)
  // data/profile.yaml turns it on by default; the Core does not report it back, so this assumes on.
  const [speakPicks, setSpeakPicks] = useState(true)
  const [log, setLog] = useState<LogEntry[]>([])
  const nextId = useRef(0)
  const scanTimer = useRef<number | undefined>(undefined)
  const { status, send } = useSocket('/ws/input')

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
  const longClench = useCallback(
    () => emit({ type: 'LONG_CLENCH', t: now(), duration: LONG_CLENCH_S }),
    [emit],
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
        }, LONG_CLENCH_S * 1000)
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
  }, [clench, doubleBlink, longClench])

  const onScanChange = (ms: number) => {
    setScanMs(ms)
    // Send once the slider settles instead of on every step.
    window.clearTimeout(scanTimer.current)
    scanTimer.current = window.setTimeout(
      () => emit({ type: 'SETTINGS', pointing_mode: 'auto', scan_ms: ms }),
      250,
    )
  }

  const toggleLang = () =>
    emit({ type: 'SETTINGS', pointing_mode: 'auto', scan_ms: scanMs, lang: lang === 'en' ? 'es' : 'en' })

  const toggleSpeakPicks = () => {
    const next = !speakPicks
    setSpeakPicks(next)
    emit({ type: 'SETTINGS', pointing_mode: 'auto', scan_ms: scanMs, speak_picks: next })
  }

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
        <StatusDot status={status} label="Input" /> Dev <span className="text-zinc-500">`</span>
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
        Space = clench · hold Space {LONG_CLENCH_S} s = long clench · B = double blink
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

      <label className="mb-3 block">
        <span className="flex justify-between text-xs text-zinc-400">
          <span>Scan speed</span>
          <span>{scanMs} ms / tile</span>
        </span>
        <input
          type="range"
          min={300}
          max={3000}
          step={100}
          value={scanMs}
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
          <span className={speakPicks ? 'text-zinc-400' : 'text-yellow-300'}>Off</span>
        </button>
      </div>

      <p className="mb-3 text-xs text-zinc-400">
        Voice: <span className="text-zinc-200">{voiceSource ?? 'nothing said yet'}</span>
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
