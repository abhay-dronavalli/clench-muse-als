import { useEffect, useRef, useState } from 'react'
import type { InputEvent as GestureEvent, Message } from '../contracts'
import { useSocket } from '../lib/useSocket'

/**
 * The input log: press "/" to slide a thin full-width strip over the top of the board showing every
 * gesture the Core received, from the headband or the keyboard stand-in.
 *
 * It shows REFUSED gestures too (in amber, with the reason). "Is the headband picking up my double
 * blink?" and "did the Core act on it?" are different questions, and a log that only showed accepted
 * gestures could not tell them apart: a paused switch and a detector that never fired look identical.
 *
 * It listens on /ws/console because INPUT_EVENT goes to consoles and input clients only — the same
 * thing the board's Muse panel does. Nothing here is ever sent to the Core.
 */

const KEPT = 60  // a strip this short shows ~6; the rest is scroll-back for the last minute or so

const LABEL: Record<GestureEvent['kind'], string> = {
  CLENCH: 'CLENCH',
  LONG_CLENCH: 'LONG CLENCH',
  DOUBLE_BLINK: 'DOUBLE BLINK',
}

function detail(event: GestureEvent): string {
  if (event.kind === 'CLENCH' && event.strength != null) return `strength ${event.strength.toFixed(2)}`
  if (event.kind === 'LONG_CLENCH' && event.duration != null) return `held ${event.duration.toFixed(1)}s`
  return ''
}

const clock = (t: number) =>
  new Date(t * 1000).toLocaleTimeString([], { hour12: false }) + `.${String(Math.floor((t % 1) * 10))}`

export function InputLog() {
  const [open, setOpen] = useState(false)
  const [events, setEvents] = useState<GestureEvent[]>([])
  const list = useRef<HTMLDivElement | null>(null)

  useSocket('/ws/console', {
    onMessage: (msg: Message) => {
      if (msg.type === 'INPUT_EVENT') setEvents((prev) => [...prev, msg].slice(-KEPT))
    },
  })

  // "/" toggles. Ignored while typing so the dev panel's fields keep working.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (e.key === '/') {
        e.preventDefault()
        setOpen((was) => !was)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (open) list.current?.scrollTo({ top: list.current.scrollHeight })
  }, [open, events])

  if (!open) return null
  const counts = events.reduce<Record<string, number>>((acc, e) => {
    if (e.accepted) acc[e.kind] = (acc[e.kind] ?? 0) + 1
    return acc
  }, {})
  return (
    <aside aria-label="Input log"
      className="fixed inset-x-0 top-0 z-50 h-28 border-b border-zinc-700 bg-black/90 text-zinc-200 backdrop-blur">
      <div className="flex items-center justify-between border-b border-zinc-800 px-3 py-1 text-[11px] text-zinc-400">
        <span className="font-semibold text-zinc-200">Input log</span>
        <span className="tabular-nums">
          {(['CLENCH', 'LONG_CLENCH', 'DOUBLE_BLINK'] as const).map((kind) => (
            <span key={kind} className="ml-3">{LABEL[kind]} {counts[kind] ?? 0}</span>
          ))}
          <span className="ml-4 text-zinc-500">/ or Esc to close</span>
        </span>
      </div>
      <div ref={list} className="h-[calc(7rem-1.75rem)] overflow-y-auto px-3 py-1 font-mono text-[11px] leading-snug">
        {events.length === 0 ? (
          <p className="text-zinc-500">Waiting for a gesture. Clench, hold, or blink twice.</p>
        ) : (
          events.map((event, i) => (
            <div key={i} className={event.accepted ? 'text-emerald-400' : 'text-amber-400'}>
              <span className="text-zinc-500">{clock(event.t)}</span>{' '}
              <span className="text-zinc-500">[{event.source}]</span>{' '}
              <span className="font-semibold">{LABEL[event.kind]}</span>{' '}
              {detail(event)}
              {event.accepted ? '' : ` — ignored: ${event.reason ?? 'unknown'}`}
            </div>
          ))
        )}
      </div>
    </aside>
  )
}
