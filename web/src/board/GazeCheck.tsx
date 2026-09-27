import { useEffect, useRef, useState } from 'react'
import type { Lang } from '../contracts'
import { gaze } from '../facetrack/gaze'

/**
 * After eye calibration: a quick check that the gaze lands where the rider looks. Two big targets
 * light up in turn ("look at the green one"); holding the gaze on it for DWELL_MS passes it. Both
 * passed = the check passes. Not done within TIMEOUT_MS = failed: the onboarding offers a redo.
 */

const DWELL_MS = 600
const TIMEOUT_MS = 10_000
export type GazeCheckResult = 'checking' | 'passed' | 'failed'

const TEXT = {
  en: { look: 'Look at the green target', passed: 'Your eyes land where you look.', failed: 'The gaze did not land on the targets.' },
  es: { look: 'Mira el objetivo verde', passed: 'Tu mirada llega a donde miras.', failed: 'La mirada no llegó a los objetivos.' },
}

export function GazeCheck({ lang, onResult, targets = 2 }: { lang: Lang; onResult: (r: GazeCheckResult) => void; targets?: 1 | 2 }) {
  const [target, setTarget] = useState(0) // 0 = left, 1 = right, 2 = done
  const [inside, setInside] = useState(false)
  const [result, setResult] = useState<GazeCheckResult>('checking')
  const since = useRef<number | null>(null)
  const targetRef = useRef(0)
  const report = useRef(onResult)
  useEffect(() => {
    report.current = onResult
  }, [onResult])

  useEffect(() => {
    const started = performance.now()
    const stop = gaze.subscribe((s) => {
      if (targetRef.current > targets - 1) return
      const p = s.found ? s.point : null
      const onTarget = p !== null && p.y > 0.25 && p.y < 0.75 &&
        (targets === 1 ? p.x > 0.3 && p.x < 0.7 : targetRef.current === 0 ? p.x < 0.4 : p.x > 0.6)
      setInside(onTarget)
      if (!onTarget) {
        since.current = null
        return
      }
      since.current ??= s.t
      if (s.t - since.current >= DWELL_MS) {
        since.current = null
        targetRef.current += 1
        setTarget(targetRef.current)
        if (targetRef.current > targets - 1) {
          setResult('passed')
          report.current('passed')
        }
      }
    })
    const timer = window.setTimeout(() => {
      if (targetRef.current <= targets - 1) {
        targetRef.current = 3
        setResult('failed')
        report.current('failed')
      }
    }, TIMEOUT_MS)
    return () => {
      stop()
      window.clearTimeout(timer)
      void started
    }
  }, [targets])

  const t = TEXT[lang]
  return (
    <div className="flex w-full max-w-5xl flex-col items-center gap-4">
      <p className="text-3xl font-bold text-[#d4a017]">{result === 'checking' ? t.look : result === 'passed' ? t.passed : t.failed}</p>
      <div className={`flex w-full ${targets === 1 ? 'justify-center' : 'justify-between'}`}>
        {(targets === 1 ? [0] : [0, 1]).map((i) => {
          const active = result === 'checking' && target === i
          const done = target > i || result === 'passed'
          return (
            <div
              key={i}
              className={[
                'flex h-40 w-40 items-center justify-center rounded-full text-5xl font-bold transition-colors',
                done ? 'bg-teal-600 text-white' : active ? (inside ? 'bg-green-500 text-white ring-8 ring-green-200' : 'bg-green-400 text-white') : 'bg-zinc-200 text-zinc-500',
              ].join(' ')}
            >
              {done ? '✓' : i + 1}
            </div>
          )
        })}
      </div>
    </div>
  )
}
