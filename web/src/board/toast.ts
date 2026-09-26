import { useCallback, useEffect, useRef, useState } from 'react'
import type { ActionResult, Lang } from '../contracts'
import { STRINGS } from './strings'

/** How long an ACTION_RESULT toast stays up. */
export const TOAST_MS = 4000
const MAX_DETAIL = 90

export type Tone = 'ok' | 'demo' | 'error'

export interface Toast {
  id: number
  tone: Tone
  text: string
}

/** Text and colour for an ACTION_RESULT, in the board's language. */
export function toastFor(r: ActionResult, lang: Lang): Omit<Toast, 'id'> {
  const s = STRINGS[lang].toast
  const who = r.contact ?? ''
  if (r.ok && r.detail === 'dry run') return { tone: 'demo', text: s.demo[r.action](who) }
  if (r.ok) return { tone: 'ok', text: s.ok[r.action](who) }
  const detail = r.detail.length > MAX_DETAIL ? r.detail.slice(0, MAX_DETAIL - 1) + '…' : r.detail
  return { tone: 'error', text: `${s.failed}: ${detail}` }
}

/** A list of toasts; each one removes itself after TOAST_MS. */
export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)
  const timers = useRef(new Set<number>())

  useEffect(() => {
    const pending = timers.current
    return () => pending.forEach((t) => window.clearTimeout(t))
  }, [])

  const push = useCallback((toast: Omit<Toast, 'id'>) => {
    const id = nextId.current++
    setToasts((ts) => [...ts, { ...toast, id }])
    const timer = window.setTimeout(() => {
      timers.current.delete(timer)
      setToasts((ts) => ts.filter((t) => t.id !== id))
    }, TOAST_MS)
    timers.current.add(timer)
  }, [])

  return { toasts, push }
}
