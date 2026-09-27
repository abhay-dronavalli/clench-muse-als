import { useEffect, useRef, useState } from 'react'
import type { Lang } from '../contracts'
import { eyedidWeb } from './eyedidWeb'

// Eyedid web calibration: five targets where the SDK asks for them. Each target is drawn first, then
// (after a short settle, so the eyes have arrived) the SDK collects samples while a ring fills. The
// result is saved in this browser (eyedidWeb.ts). Esc cancels. The board gets no gaze meanwhile.

const TEXT = {
  en: { look: 'Look at the dot until its ring fills', saved: 'Eye calibration saved', off: 'Eyedid web is not running', esc: 'Esc = cancel' },
  es: { look: 'Mira el punto hasta que se llene el círculo', saved: 'Calibración de ojos guardada', off: 'Eyedid web no está funcionando', esc: 'Esc = cancelar' },
} as const

const SETTLE_MS = 400 // eyes need a moment to land on a new target before samples count

type Phase =
  | { kind: 'point'; x: number; y: number; progress: number }
  | { kind: 'starting' }
  | { kind: 'saved' }
  | { kind: 'failed'; reason: string }

export function EyeCalibrationOverlay({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const t = TEXT[lang]
  const [phase, setPhase] = useState<Phase>({ kind: 'starting' })
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    let settle: number | undefined
    let finished = false
    let unmounted = false // React dev mounts effects twice: the first run's cancel must not close the overlay
    const started = eyedidWeb.calibrate({
      point: (x, y, ready) => {
        if (unmounted) return
        setPhase({ kind: 'point', x, y, progress: 0 })
        window.clearTimeout(settle)
        settle = window.setTimeout(ready, SETTLE_MS)
      },
      progress: (p) => !unmounted && setPhase((was) => (was.kind === 'point' ? { ...was, progress: p } : was)),
      done: (ok) => {
        finished = true
        if (unmounted) return
        if (ok) {
          setPhase({ kind: 'saved' })
          window.setTimeout(() => close.current(), 1200)
        } else {
          close.current()
        }
      },
    })
    if (!started) {
      const why = eyedidWeb.state === 'on' ? 'the SDK would not start calibrating' : `${eyedidWeb.state}${eyedidWeb.detail ? `: ${eyedidWeb.detail}` : ''}`
      setPhase({ kind: 'failed', reason: why })
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.preventDefault()
      if (started && !finished) eyedidWeb.cancelCalibration()
      else close.current()
    }
    window.addEventListener('keydown', onKey)
    return () => {
      unmounted = true
      window.clearTimeout(settle)
      window.removeEventListener('keydown', onKey)
      if (started && !finished) eyedidWeb.cancelCalibration()
    }
  }, [])

  return (
    <div role="dialog" aria-label="Eye calibration" className="fixed inset-0 z-50 bg-black text-white">
      {phase.kind === 'point' && (
        <div className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: phase.x, top: phase.y }}>
          <svg width="96" height="96" viewBox="0 0 96 96" aria-hidden="true">
            <circle cx="48" cy="48" r="40" fill="none" stroke="rgb(63 63 70)" strokeWidth="8" />
            <circle cx="48" cy="48" r="40" fill="none" stroke="rgb(56 189 248)" strokeWidth="8" strokeLinecap="round"
              strokeDasharray={2 * Math.PI * 40} strokeDashoffset={2 * Math.PI * 40 * (1 - phase.progress)}
              transform="rotate(-90 48 48)" />
            <circle cx="48" cy="48" r="10" fill="rgb(239 68 68)" />
          </svg>
        </div>
      )}
      <div className="pointer-events-none absolute inset-x-0 bottom-10 text-center">
        {phase.kind === 'point' && <p className="text-3xl font-semibold text-zinc-300">{t.look}</p>}
        {phase.kind === 'saved' && <p className="text-4xl font-bold text-emerald-400">{t.saved}</p>}
        {phase.kind === 'failed' && <p className="text-3xl font-semibold text-amber-300">{t.off} ({phase.reason})</p>}
        <p className="mt-3 text-lg text-zinc-500">{t.esc}</p>
      </div>
    </div>
  )
}
