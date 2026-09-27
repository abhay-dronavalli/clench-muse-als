import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import type { Lang, Signal } from '../contracts'
import { gaze } from '../facetrack/gaze'
import { nativeBridge, nativeEvents } from '../facetrack/native'
import { useSocket } from '../lib/useSocket'
import { museFresh } from '../sensor/status'
import { GazeCheck, type GazeCheckResult } from './GazeCheck'
import { signalReady } from './onboardingFlow'

/**
 * Car mode's short onboarding (decisions #30), in the app's white theme with the tablet's 3D car
 * revolving behind it (the shell's car preview). It advances by its timers or by touch, never by
 * blinks; the Core ignores ordinary input meanwhile (SETTINGS onboarding) but help still works.
 *
 *   intro   5 s  "Getting your ride ready"
 *   eyes         a one-target gaze check; a miss offers a full recalibration (touch)
 *   band         already connected: "Headband connected" and on; else up to 30 s, then on
 *   clench       only with a headband: one clench past the threshold line
 */

type Step = 'intro' | 'eyes' | 'band' | 'clench'
const STEP_MS: Record<Step, number> = { intro: 5000, eyes: 15000, band: 30000, clench: 20000 }
const CONNECTED_PAUSE_MS = 1500

const TEXT = {
  en: {
    introTitle: 'Getting your ride ready', introText: 'A quick check of your eyes and headband.',
    eyesTitle: 'Your eyes', recalibrate: 'Recalibrate', noEyes: 'No eye tracker here: touch and scan still work.',
    bandTitle: 'Your headband', bandOk: 'Headband connected', bandWait: 'Looking for your headband…', bandNo: 'No headband: continuing with touch and gaze.',
    clenchTitle: 'A test clench', clenchText: 'Clench until the bar passes the black line.', clenchOk: 'That worked.',
    next: 'Next', skip: 'Skip', go: 'Go', left: (s: number) => `Next in ${s} s`,
  },
  es: {
    introTitle: 'Preparando tu viaje', introText: 'Una revisión rápida de tus ojos y la banda.',
    eyesTitle: 'Tus ojos', recalibrate: 'Recalibrar', noEyes: 'No hay seguidor de ojos: el toque y el escaneo siguen funcionando.',
    bandTitle: 'Tu banda', bandOk: 'Banda conectada', bandWait: 'Buscando tu banda…', bandNo: 'Sin banda: seguimos con toque y mirada.',
    clenchTitle: 'Una prueba', clenchText: 'Aprieta hasta pasar la línea negra.', clenchOk: 'Funcionó.',
    next: 'Siguiente', skip: 'Omitir', go: 'Listo', left: (s: number) => `Sigue en ${s} s`,
  },
}

export function CarOnboarding({ lang, paused, onDone }: { lang: Lang; paused: boolean; onDone: () => void }) {
  const t = TEXT[lang]
  const native = nativeBridge()
  const [step, setStep] = useState<Step>('intro')
  const [until, setUntil] = useState(() => performance.now() + STEP_MS.intro)
  const [now, setNow] = useState(() => performance.now())
  const [signal, setSignal] = useState<Signal | null>(null)
  const [clenched, setClenched] = useState(false)
  const [check, setCheck] = useState<GazeCheckResult | null>(null)
  const [calibrating, setCalibrating] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const [wall, setWall] = useState(() => Date.now() / 1000)
  const stepStartWall = useRef(0) // a clench counts only after the clench step opened
  const done = useRef(false)
  const connectedAt = useRef<number | null>(null)

  const finish = useCallback(() => {
    if (done.current) return
    done.current = true
    onDone()
  }, [onDone])
  const go = useCallback((next: Step | 'done') => {
    if (next === 'done') return finish()
    setStep(next)
    setUntil(performance.now() + STEP_MS[next])
    stepStartWall.current = Date.now() / 1000
    connectedAt.current = null
  }, [finish])

  // The 3D car revolves behind this screen on the tablet.
  useEffect(() => {
    native?.carPreview?.(true)
    return () => native?.carPreview?.(false)
  }, [native])

  useSocket('/ws/console', {
    onMessage: (msg) => {
      if (msg.type === 'SIGNAL') setSignal(msg)
      if (msg.type === 'INPUT_EVENT' && msg.source === 'muse' && msg.kind === 'CLENCH' && msg.t >= stepStartWall.current) setClenched(true)
    },
  })

  useEffect(() => nativeEvents.subscribe((e) => {
    if (e.type === 'calibration' && (e.state === 'finished' || e.state === 'canceled')) {
      setCalibrating(false)
      setCheck(null)
      setAttempt((a) => a + 1) // check the gaze again after a recalibration
    }
  }), [])

  // Timers pause while disconnected or during help.
  useEffect(() => {
    let previous = performance.now()
    const timer = window.setInterval(() => {
      const at = performance.now()
      if (paused || calibrating) setUntil((u) => u + at - previous)
      previous = at
      setNow(at)
      setWall(Date.now() / 1000)
    }, 100)
    return () => window.clearInterval(timer)
  }, [paused, calibrating])

  const connected = museFresh(signal, wall)
  const good = signalReady(signal, wall)
  const hasGaze = gaze.connected()

  const goRef = useRef(go)
  useLayoutEffect(() => { goRef.current = go })

  // Advance: by the step's own success, or by its timer.
  useEffect(() => {
    if (paused || calibrating) return
    const next = goRef.current
    const expired = now >= until
    if (step === 'intro' && expired) next('eyes')
    else if (step === 'eyes' && (check === 'passed' || expired || (!hasGaze && now >= until - STEP_MS.eyes + 3000))) next('band')
    else if (step === 'band') {
      if (connected) {
        connectedAt.current ??= now
        if (now - connectedAt.current >= CONNECTED_PAUSE_MS) next('clench')
      } else if (expired) next('done')
    } else if (step === 'clench' && ((clenched && good) || expired)) next('done')
  }, [now, until, step, check, connected, clenched, good, paused, calibrating, hasGaze])

  const recalibrate = () => {
    setCalibrating(true)
    try { native?.calibrate('patient') } catch { setCalibrating(false) }
  }
  const left = Math.max(0, Math.ceil((until - now) / 1000))
  const titles = { intro: t.introTitle, eyes: t.eyesTitle, band: t.bandTitle, clench: t.clenchTitle }
  type ButtonId = 'next' | 'recalibrate'
  const buttons: { id: ButtonId; label: string }[] =
    step === 'intro' ? [{ id: 'next', label: t.next }] :
    step === 'eyes' ? [...(check === 'failed' && typeof native?.calibrate === 'function' ? [{ id: 'recalibrate' as const, label: t.recalibrate }] : []), { id: 'next', label: check === 'failed' ? t.next : t.skip }] :
    step === 'band' ? [{ id: 'next', label: t.skip }] :
    [{ id: 'next', label: clenched && good ? t.go : t.skip }]
  const press = (id: ButtonId) => {
    if (id === 'recalibrate') recalibrate()
    else go(step === 'intro' ? 'eyes' : step === 'eyes' ? 'band' : 'done')
  }

  return (
    // On the tablet the page is see-through: the 3D car revolves behind. Elsewhere, a white backdrop.
    <div style={{ display: paused ? 'none' : undefined }}
      className={`fixed inset-0 z-40 flex flex-col items-center justify-between p-6 text-zinc-900 ${typeof native?.carPreview === 'function' ? '' : 'bg-[linear-gradient(to_bottom,#ffffff_0%,#eef3f8_100%)]'}`}>
      <header className="w-full max-w-5xl rounded-3xl bg-white/95 px-8 py-6 text-center shadow-lg">
        <h1 className="text-5xl font-bold">{titles[step]}</h1>
        {step === 'intro' && <p className="mt-3 text-3xl text-zinc-600">{t.introText}</p>}
      </header>
      <main className="flex w-full max-w-5xl flex-1 items-center justify-center py-4">
        {step === 'eyes' && (hasGaze
          ? <div className="w-full rounded-3xl bg-white/90 p-6">{!calibrating && <GazeCheck key={attempt} lang={lang} targets={1} onResult={setCheck} />}</div>
          : <p className="rounded-3xl bg-white/95 px-8 py-6 text-3xl">{t.noEyes}</p>)}
        {step === 'band' && (
          <p className={`rounded-3xl px-10 py-8 text-5xl font-bold shadow-lg ${connected ? 'bg-emerald-600 text-white' : 'bg-white/95 text-zinc-800'}`}>
            {connected ? `✓ ${t.bandOk}` : now >= until - 1000 ? t.bandNo : t.bandWait}
          </p>
        )}
        {step === 'clench' && (
          <div className="w-full max-w-2xl rounded-3xl bg-white/95 p-8 text-center">
            <p className="mb-5 text-3xl">{clenched && good ? t.clenchOk : t.clenchText}</p>
            <div className="relative h-6 w-full overflow-hidden rounded-full bg-zinc-200">
              <div className="h-full rounded-full bg-sky-600 transition-[width] duration-150"
                style={{ width: `${Math.min(1, (signal?.emg ?? 0) / ((signal?.threshold ?? 1) * 2)) * 100}%` }} />
              <div className="absolute inset-y-0 left-1/2 w-1.5 -translate-x-1/2 bg-zinc-900" aria-hidden />
            </div>
          </div>
        )}
      </main>
      <footer className="flex w-full max-w-5xl flex-col items-center gap-4 rounded-3xl bg-white/95 px-8 py-6 shadow-lg">
        <div className="flex flex-wrap justify-center gap-6">
          {buttons.map((b) => (
            <button key={b.id} type="button" onClick={() => press(b.id)}
              className="min-h-24 min-w-64 rounded-3xl bg-zinc-900 px-10 py-6 text-4xl font-bold text-white shadow-lg">
              {b.label}
            </button>
          ))}
        </div>
        <p className="text-2xl tabular-nums text-zinc-600">{calibrating ? '…' : t.left(left)}</p>
      </footer>
    </div>
  )
}
