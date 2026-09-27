import { useCallback, useEffect, useRef, useState } from 'react'
import type { CarActionName, WindowName } from '../contracts'

/**
 * The trip screen's confirm sequence (CAR_ACTION), driven by the event itself:
 *   phase 'out'  the other five tiles fade out, the picked one stays (and grows a little)
 *   phase 'in'   FADE_MS before the Core unlocks, everything fades back in
 *   tint         Pull over only: a warm copper tint that lasts through its spoken line, then fades
 */

export interface CarAnim {
  action: CarActionName
  window: WindowName | null
  ms: number
}

export const FADE_MS = 350 // tiles out, then back in: well inside the routine sequence's ~900 ms
const TINT_AFTER_MS = 2500 // Pull over's tint stays this long past its sequence (the sentence is said)

export function useCarAnimation() {
  const [anim, setAnim] = useState<CarAnim | null>(null)
  const [phase, setPhase] = useState<'in' | 'out'>('in')
  const [tint, setTint] = useState(false)
  const timers = useRef<number[]>([])

  const clear = () => {
    timers.current.forEach((t) => window.clearTimeout(t))
    timers.current = []
  }

  const start = useCallback((action: CarActionName, ms: number, which: WindowName | null = null) => {
    clear()
    setAnim({ action, window: which, ms })
    setPhase('out')
    const later = (fn: () => void, after: number) => timers.current.push(window.setTimeout(fn, after))
    later(() => setPhase('in'), Math.max(ms - FADE_MS, FADE_MS))
    later(() => setAnim(null), ms)
    if (action === 'pull_over') {
      setTint(true)
      later(() => setTint(false), ms + TINT_AFTER_MS)
    }
  }, [])

  useEffect(() => clear, [])

  return { anim, phase, tint, start }
}
