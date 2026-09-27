import { useEffect, useRef, useState } from 'react'
import type { HeadRange, Lang } from '../contracts'
import { STRINGS } from '../board/strings'
import { DOT, emptySamples, rangeFromSamples, SETTLE_MS, STEP_MS, STEPS, type CalibrationProblem, type Step } from './calibrate'
import { saveHeadRange } from './headRange'
import { useTrackerStatus } from './useTrackerStatus'
import { tracker } from './tracker'

const READY_MS = 1500 // "get ready" before the first dot
const RESULT_MS = 2500 // how long "saved" or the problem stays up

type Phase =
  | { kind: 'ready' }
  | { kind: 'step'; step: Step; index: number }
  | { kind: 'saving' }
  | { kind: 'saved' }
  | { kind: 'failed'; problem: CalibrationProblem | 'save' }

/**
 * Head-range calibration (PRD A3.3a "calibrate center and edges", A7 head_range): a big dot at the
 * center, then the left, right, top and bottom edges, 1.5 s each; the person turns the head
 * comfortably toward it. The medians become the range, saved to the Core so it survives reloads.
 * Esc cancels. Nothing is sent to the Core but the result (no video, no frames).
 */
export function CalibrationOverlay({ lang, onSaved, onClose }: { lang: Lang; onSaved: (r: HeadRange) => void; onClose: () => void }) {
  const s = STRINGS[lang].calibrate
  const [phase, setPhase] = useState<Phase>({ kind: 'ready' })
  const [progress, setProgress] = useState(0) // 0..1 within the current step
  const cameraOn = useTrackerStatus().kind === 'on'
  const done = useRef({ onSaved, onClose })
  useEffect(() => {
    done.current = { onSaved, onClose }
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') done.current.onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  useEffect(() => {
    if (!cameraOn) return
    const samples = emptySamples()
    const start = performance.now()
    const stepAt = (t: number) => {
      const i = Math.floor((t - start - READY_MS) / STEP_MS)
      return { i, into: t - start - READY_MS - i * STEP_MS }
    }
    const stop = tracker.subscribeSample((sample) => {
      const { i, into } = stepAt(sample.t)
      if (i >= 0 && i < STEPS.length && into >= SETTLE_MS && sample.raw) samples[STEPS[i]].push(sample.raw)
    })
    let finished = false
    const timer = window.setInterval(() => {
      const { i, into } = stepAt(performance.now())
      if (i < 0) return
      if (i < STEPS.length) {
        setPhase((p) => (p.kind === 'step' && p.index === i ? p : { kind: 'step', step: STEPS[i], index: i }))
        setProgress(into / STEP_MS)
        return
      }
      if (finished) return
      finished = true
      window.clearInterval(timer)
      stop()
      const result = rangeFromSamples(samples)
      if (!result.ok) {
        setPhase({ kind: 'failed', problem: result.problem })
        return
      }
      setPhase({ kind: 'saving' })
      saveHeadRange(result.range).then(
        (saved) => {
          done.current.onSaved(saved)
          setPhase({ kind: 'saved' })
        },
        (e) => {
          console.warn('head range not saved', e)
          setPhase({ kind: 'failed', problem: 'save' })
        },
      )
    }, 50)
    return () => {
      window.clearInterval(timer)
      stop()
    }
  }, [cameraOn])

  // Close by itself once the result has been read.
  useEffect(() => {
    if (phase.kind !== 'saved' && phase.kind !== 'failed') return
    const t = window.setTimeout(() => done.current.onClose(), RESULT_MS + (phase.kind === 'failed' ? 1500 : 0))
    return () => window.clearTimeout(t)
  }, [phase.kind])

  let message: string
  if (!cameraOn) message = s.cameraOff
  else if (phase.kind === 'ready') message = s.ready
  else if (phase.kind === 'step') message = `${s.look}: ${s.steps[phase.step]}`
  else if (phase.kind === 'saving') message = s.saving
  else if (phase.kind === 'saved') message = s.saved
  else if (phase.problem === 'save') message = s.saveFailed
  else if (phase.problem.kind === 'no_face') message = s.noFace(s.steps[phase.problem.step])
  else message = s.tooSmall[phase.problem.axis]

  const dot = phase.kind === 'step' ? DOT[phase.step] : phase.kind === 'ready' ? DOT.center : null
  return (
    <div role="dialog" aria-label={s.look} className="fixed inset-0 z-[60] bg-black/95 text-white">
      <p className="absolute inset-x-0 top-[30%] text-center text-5xl font-bold">{message}</p>
      <p className="absolute right-8 top-6 text-2xl text-zinc-400">{s.cancel}</p>
      {dot && (
        <div
          className="absolute -translate-x-1/2 -translate-y-1/2 transition-all duration-300"
          style={{ left: `${dot.x * 100}%`, top: `${dot.y * 100}%` }}
        >
          <div className="h-20 w-20 rounded-full bg-yellow-300 ring-8 ring-yellow-300/30" />
          {phase.kind === 'step' && (
            <div
              className="absolute inset-0 rounded-full ring-4 ring-white"
              style={{ transform: `scale(${1.8 - 0.8 * Math.min(progress, 1)})`, opacity: 0.8 }}
            />
          )}
        </div>
      )}
    </div>
  )
}
