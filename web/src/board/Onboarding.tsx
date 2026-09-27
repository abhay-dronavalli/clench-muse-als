import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { GazeCheck, type GazeCheckResult } from './GazeCheck'
import type { Lang, Signal } from '../contracts'
import { nativeBridge, nativeEvents } from '../facetrack/native'
import { useSocket } from '../lib/useSocket'
import { getSensor, startSensor } from '../sensor/service'
import { STRINGS } from './strings'
import { CALIBRATE_AFTER_MS, canEnableMuse, nextSetupStep, NEXT_STEP, NO_EYES_MS, signalReady, STEP_MS, type SetupStep } from './onboardingFlow'
import { CameraLight } from '../facetrack/indicators'
import { museFresh } from '../sensor/status'
import { unlockSpeech } from './speech'
import { useSetupInput, type SetupOption } from './useSetupInput'

/** Setup owns local gaze/blinks. Timers skip unavailable inputs; only a verified clench enables Muse. */
export function Onboarding({ lang, paused, onDone }: { lang: Lang; paused: boolean; onDone: (muse: boolean) => void }) {
  const s = STRINGS[lang].setup
  const [phase, setPhase] = useState(() => ({ step: 'welcome' as SetupStep, duration: STEP_MS.welcome, until: performance.now() + STEP_MS.welcome, wall: Date.now() / 1000 }))
  const [now, setNow] = useState(() => performance.now())
  const [wallNow, setWallNow] = useState(() => Date.now() / 1000)
  const [signal, setSignal] = useState<Signal | null>(null)
  const [eyes, setEyes] = useState<'idle' | 'calibrating' | 'finished' | 'failed'>('idle')
  const [progress, setProgress] = useState(0)
  const [trackerReady, setTrackerReady] = useState(() => nativeBridge()?.gazeReady?.() ?? false)
  const [bandError, setBandError] = useState('')
  const [clenched, setClenched] = useState(false)
  const [retry, setRetry] = useState(0)
  const latest = useRef({ phase, signal, paused })
  useLayoutEffect(() => { latest.current = { phase, signal, paused } })
  const native = nativeBridge()
  const step = phase.step
  const attempted = useRef(false)
  const done = useRef(false)
  const goodSince = useRef<number | null>(null)
  const hasEyes = typeof native?.calibrate === 'function' && typeof native?.cancelCalibration === 'function'
  // One look: onboarding never shows the car (car visuals only in Car mode). The calibration dots are
  // drawn by the tablet's native overlay above this page, so the page stays opaque.
  const preview = false
  const [check, setCheck] = useState<GazeCheckResult | null>(null) // after calibration: does the gaze land?

  useSocket('/ws/console', {
    onMessage: (msg) => {
      if (msg.type === 'SIGNAL') setSignal(msg)
      const state = latest.current
      if (msg.type === 'INPUT_EVENT' && state.phase.step === 'clench' && !state.paused && !document.hidden &&
          msg.source === 'muse' && msg.kind === 'CLENCH' && msg.t >= state.phase.wall &&
          msg.t <= Date.now() / 1000 + 2 && signalReady(state.signal, Date.now() / 1000)) setClenched(true)
    },
  })

  const go = useCallback((next: SetupStep) => {
    if (done.current) return
    if (step === 'eyes') native?.cancelCalibration?.()
    goodSince.current = null
    const duration = next === 'eyes' && !hasEyes ? NO_EYES_MS : STEP_MS[next]
    setPhase({ step: next, duration, until: performance.now() + duration, wall: Date.now() / 1000 })
    setNow(performance.now())
  }, [step, native, hasEyes])
  const finish = useCallback(() => {
    if (done.current) return
    done.current = true
    native?.cancelCalibration?.()
    onDone(canEnableMuse(clenched, signal, Date.now() / 1000))
  }, [native, onDone, clenched, signal])
  const advance = useCallback(() => { const next = NEXT_STEP[step]; if (next) go(next); else finish() }, [step, go, finish])
  const calibrate = useCallback(() => {
    if (!hasEyes || !trackerReady || eyes === 'calibrating') return
    attempted.current = true
    setProgress(0)
    setEyes('calibrating')
    setCheck(null)
    try { native?.calibrate('patient') } catch { setEyes('failed') }
  }, [hasEyes, trackerReady, eyes, native])
  const actions = useRef({ advance, go, calibrate })
  useLayoutEffect(() => { actions.current = { advance, go, calibrate } })

  // Pause the countdown while disconnected, during help, or while this page is hidden.
  useEffect(() => {
    let previous = performance.now()
    const timer = window.setInterval(() => {
      const at = performance.now()
      if (latest.current.paused || document.hidden) {
        setPhase((p) => ({ ...p, until: p.until + at - previous }))
      }
      previous = at
      setNow(at)
      setWallNow(Date.now() / 1000)
    }, 100)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => () => native?.cancelCalibration?.(), [native])

  useEffect(() => nativeEvents.subscribe((e) => {
    if (e.type === 'tracker') setTrackerReady(e.state === 'on')
    if (latest.current.phase.step !== 'eyes') return
    if (e.type === 'calibration_progress') setProgress(Math.min(1, Math.max(0, e.progress)))
    if (e.type === 'calibration' && e.state === 'finished') { setEyes('finished'); setProgress(1); setCheck('checking') }
    if (e.type === 'calibration' && e.state === 'canceled') setEyes('failed')
    if (e.type === 'tracker' && e.state === 'error') setEyes('failed')
  }), [])

  // Connect once per attempt, and ignore late responses after leaving the headband step.
  useEffect(() => {
    if (step !== 'band' || paused) return
    let live = true
    void (async () => {
      setBandError('')
      try {
        const status = await getSensor()
        if (!live || status.running) return
        const profile = status.profile && status.profiles.includes(status.profile) ? status.profile : status.profiles[0]
        if (!profile) throw new Error(s.noProfile)
        await startSensor(profile, 'muse')
      } catch (e) { if (live) setBandError(e instanceof Error ? e.message : String(e)) }
    })()
    return () => { live = false }
  }, [step, retry, paused, s.noProfile])

  const good = signalReady(signal, wallNow)
  useEffect(() => {
    if (paused || document.hidden) { goodSince.current = null; return }
    if (step === 'eyes' && !attempted.current && hasEyes && trackerReady &&
        now >= phase.until - STEP_MS.eyes + CALIBRATE_AFTER_MS) actions.current.calibrate()
    if (step === 'band') {
      if (!good) goodSince.current = null
      else {
        goodSince.current ??= now
      }
    }
    const next = nextSetupStep(step, now >= phase.until && !(step === 'eyes' && eyes === 'finished' && check === 'checking'), eyes === 'finished' && check === 'passed',
      goodSince.current === null ? 0 : now - goodSince.current, clenched && good)
    if (next === 'exit') actions.current.advance()
    else if (next) actions.current.go(next)
  }, [now, phase.until, step, paused, eyes, good, clenched, hasEyes, trackerReady, check])

  const options: SetupOption[] = step === 'eyes' ? [
    ...(hasEyes && trackerReady && eyes !== 'calibrating' && eyes !== 'finished' ? [{ id: 'calibrate', label: eyes === 'failed' ? s.tryAgain : s.eyesStart, act: () => calibrate() }] : []),
    ...(eyes === 'finished' && check === 'failed' ? [
      { id: 'redo', label: lang === 'es' ? 'Repetir calibración' : 'Redo calibration', act: () => { attempted.current = false; setEyes('idle'); calibrate() } },
      { id: 'continue-eyes', label: lang === 'es' ? 'Seguir igual' : 'Continue anyway', act: () => go('band') },
    ] : [{ id: 'skip-eyes', label: s.skip, act: () => go('band') }]),
  ] : step === 'band' ? [
    ...(bandError ? [{ id: 'retry', label: s.tryAgain, act: () => setRetry((v) => v + 1) }] : []),
    { id: 'skip-band', label: s.skipBand, act: () => go('done') },
  ] : step === 'clench' ? [{ id: 'skip-clench', label: s.skipBand, act: () => go('done') }] :
    [{ id: 'continue', label: step === 'welcome' ? s.begin : s.go, act: () => advance() }]
  const input = useSetupInput(step, options, !paused)
  const remaining = Math.max(0, Math.ceil((phase.until - now) / 1000))
  const timedProgress = Math.max(0, Math.min(1, 1 - (phase.until - now) / phase.duration))
  const titles = { welcome: s.welcomeTitle, eyes: s.eyesTitle, band: s.bandTitle, clench: s.clenchTitle, done: s.doneTitle }
  const text = step === 'welcome' ? s.welcomeText : step === 'eyes' ? (hasEyes ? s.orbitHint : s.eyesNone) :
    step === 'band' ? s.bandText : step === 'clench' ? s.clenchText : (clenched && good ? s.doneText : s.doneScanning)
  const nextLabel = step === 'welcome' ? s.stepEyes : step === 'eyes' ? s.stepBand : s.go

  return (
    <div onPointerDownCapture={unlockSpeech} style={{ display: paused ? 'none' : undefined }} className={`fixed inset-0 z-40 flex flex-col p-5 text-amber-50 sm:p-8 ${preview ? '' : 'bg-black'}`}>
      <header className="mx-auto w-full max-w-5xl rounded-3xl bg-zinc-900/95 px-8 py-5 text-center shadow-sm">
        <p className="text-lg font-bold uppercase tracking-widest text-[#d4a017]">{s.brand}</p>
        <div className="absolute right-8 top-8"><CameraLight lang={lang} /></div>
        <nav aria-label={s.setupProgress} className="my-3 flex justify-center gap-4 text-xl font-semibold">
          {[s.stepEyes, s.stepBand, s.stepClench].map((label, i) => <span key={label} className={`rounded-full px-4 py-1 ${['eyes', 'band', 'clench'][i] === step ? 'bg-[#d4a017] text-white' : 'bg-zinc-800 text-zinc-300'}`}>{i + 1}. {label}</span>)}
        </nav>
        <h1 className="text-4xl font-bold sm:text-5xl">{titles[step]}</h1>
        <p className="mt-3 text-xl text-zinc-300 sm:text-2xl">{text}</p>
      </header>
      <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 py-4 text-center">
        {step === 'eyes' && eyes !== 'finished' && <div className="max-w-3xl rounded-3xl bg-zinc-900/95 px-8 py-6 text-left text-2xl leading-snug text-zinc-800 shadow-sm">
          <ol className="list-decimal space-y-2 pl-8">
            <li>{lang === 'es' ? 'Siéntate cómodo y mantén la cabeza quieta.' : 'Sit comfortably and keep your head still.'}</li>
            <li>{lang === 'es' ? 'Sigue el punto con los ojos hasta que desaparezca.' : 'Follow the dot with your eyes until it disappears.'}</li>
            <li>{lang === 'es' ? 'Luego mira los dos objetivos verdes para comprobarlo.' : 'Then look at the two green targets to check it.'}</li>
          </ol>
        </div>}
        {step === 'eyes' && eyes === 'finished' && <GazeCheck lang={lang} onResult={setCheck} />}
        {step === 'band' && <div className="rounded-3xl bg-zinc-900 p-6 shadow-sm">
          <div className="mb-5 flex flex-wrap justify-center gap-3">{['TP9', 'AF7', 'AF8', 'TP10'].map((name, i) => {
            const value = signal?.ch[i]
            const ok = museFresh(signal, wallNow) && value !== undefined && value >= 1 && value <= 200
            return <span key={name} className={`rounded-xl px-4 py-3 text-xl font-semibold ${ok ? 'bg-emerald-900 text-emerald-200' : 'bg-amber-900 text-amber-200'}`}>{name}</span>
          })}</div>
          <p className="max-w-3xl text-2xl">{bandError ? `${s.bandFailed} ${bandError}` : good ? s.bandGood : signal?.blocked || s.bandLooking}</p>
        </div>}
        {step === 'clench' && <div className="w-full max-w-xl rounded-3xl bg-zinc-900 p-8">
          <p className="mb-5 text-2xl">{clenched ? s.clenchWorked : s.clenchWaiting}</p>
          <div className="relative">
            <Meter label={s.stepClench} value={Math.min(1, (signal?.emg ?? 0) / ((signal?.threshold ?? 1) * 2))} />
            <div className="absolute -top-2 left-1/2 h-7 w-1 -translate-x-1/2 rounded bg-zinc-900" aria-hidden />
          </div>
          <p className="mt-2 text-lg text-zinc-300">{lang === 'es' ? 'Aprieta hasta pasar la línea negra (el umbral).' : 'Clench until the bar passes the black line (the threshold).'}</p>
        </div>}
      </main>
      <footer className="mx-auto w-full max-w-5xl shrink-0 rounded-3xl bg-zinc-900/95 px-8 py-5 text-center shadow-lg">
        {step === 'eyes' && <div className="mb-4"><p className="mb-2 text-lg text-[#d4a017]">{eyes === 'calibrating' ? s.eyesFollow : eyes === 'failed' ? s.eyesError : trackerReady ? s.calibrationIn(Math.max(0, Math.ceil((phase.until - phase.duration + CALIBRATE_AFTER_MS - now) / 1000))) : s.eyesStarting}</p><Meter label={s.calibrationProgress} value={progress} /></div>}
        <p className="mb-3 text-xl font-semibold text-[#d4a017]">{input.tracked ? s.lookBlink : s.scanBlink}</p>
        <SetupButtons options={options} selected={input.selected} />
        <p className="mb-2 mt-4 text-xl tabular-nums">{s.nextIn(nextLabel, remaining)}</p>
        <Meter label={s.nextStep} value={timedProgress} />
      </footer>
    </div>
  )
}

function SetupButtons({ options, selected }: { options: SetupOption[]; selected: number }) {
  return <div className="flex flex-wrap justify-center gap-4">{options.map((option, i) => <button key={option.id} data-setup-option={i} type="button" onClick={option.act}
    className={`min-h-24 min-w-64 rounded-3xl px-10 py-6 text-4xl font-bold focus-visible:outline-4 focus-visible:outline-offset-4 focus-visible:outline-amber-300 ${selected === i ? 'bg-[#d4a017] text-black ring-4 ring-amber-200' : 'bg-zinc-800 text-amber-50'}`}>
    {option.label}
  </button>)}</div>
}

export function Meter({ label, value }: { label: string; value: number }) {
  const safe = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
  return <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(safe * 100)} className="h-3 w-full overflow-hidden rounded-full bg-zinc-700">
    <div className="h-full rounded-full bg-[#d4a017] transition-[width] duration-150" style={{ width: `${safe * 100}%` }} />
  </div>
}
