import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
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
  const preview = step === 'eyes' && typeof native?.carPreview === 'function' && !paused

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

  useEffect(() => {
    if (!preview) return
    native?.carPreview?.(true)
    return () => { native?.cancelCalibration?.(); native?.carPreview?.(false) }
  }, [preview, native])

  useEffect(() => nativeEvents.subscribe((e) => {
    if (e.type === 'tracker') setTrackerReady(e.state === 'on')
    if (latest.current.phase.step !== 'eyes') return
    if (e.type === 'calibration_progress') setProgress(Math.min(1, Math.max(0, e.progress)))
    if (e.type === 'calibration' && e.state === 'finished') { setEyes('finished'); setProgress(1) }
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
    const next = nextSetupStep(step, now >= phase.until, eyes === 'finished',
      goodSince.current === null ? 0 : now - goodSince.current, clenched && good)
    if (next === 'exit') actions.current.advance()
    else if (next) actions.current.go(next)
  }, [now, phase.until, step, paused, eyes, good, clenched, hasEyes, trackerReady])

  const options: SetupOption[] = step === 'eyes' ? [
    ...(hasEyes && trackerReady && eyes !== 'calibrating' ? [{ id: 'calibrate', label: eyes === 'failed' ? s.tryAgain : s.eyesStart, act: () => calibrate() }] : []),
    { id: 'skip-eyes', label: s.skip, act: () => go('band') },
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
    <div onPointerDownCapture={unlockSpeech} style={{ display: paused ? 'none' : undefined }} className={`fixed inset-0 z-40 flex flex-col p-5 text-zinc-900 sm:p-8 ${preview ? '' : 'bg-[linear-gradient(160deg,#eaf6f4_0%,#f6f8f8_45%,#e3eef7_100%)]'}`}>
      <header className="mx-auto w-full max-w-5xl rounded-3xl bg-white/95 px-8 py-5 text-center shadow-sm">
        <p className="text-lg font-bold uppercase tracking-widest text-[#007a72]">{s.brand}</p>
        <div className="absolute right-8 top-8"><CameraLight lang={lang} /></div>
        <nav aria-label={s.setupProgress} className="my-3 flex justify-center gap-4 text-xl font-semibold">
          {[s.stepEyes, s.stepBand, s.stepClench].map((label, i) => <span key={label} className={`rounded-full px-4 py-1 ${['eyes', 'band', 'clench'][i] === step ? 'bg-[#007a72] text-white' : 'bg-zinc-100 text-zinc-600'}`}>{i + 1}. {label}</span>)}
        </nav>
        <h1 className="text-4xl font-bold sm:text-5xl">{titles[step]}</h1>
        <p className="mt-3 text-xl text-zinc-600 sm:text-2xl">{text}</p>
      </header>
      <main className="flex min-h-0 flex-1 flex-col items-center justify-center gap-5 py-4 text-center">
        {step === 'eyes' && !preview && <div className="rounded-3xl bg-white/95 px-8 py-6 text-2xl text-[#007a72]">{s.previewUnavailable}</div>}
        {step === 'band' && <div className="rounded-3xl bg-white p-6 shadow-sm">
          <div className="mb-5 flex flex-wrap justify-center gap-3">{['TP9', 'AF7', 'AF8', 'TP10'].map((name, i) => {
            const value = signal?.ch[i]
            const ok = museFresh(signal, wallNow) && value !== undefined && value >= 1 && value <= 200
            return <span key={name} className={`rounded-xl px-4 py-3 text-xl font-semibold ${ok ? 'bg-teal-50 text-teal-800' : 'bg-amber-50 text-amber-800'}`}>{name}</span>
          })}</div>
          <p className="max-w-3xl text-2xl">{bandError ? `${s.bandFailed} ${bandError}` : good ? s.bandGood : signal?.blocked || s.bandLooking}</p>
        </div>}
        {step === 'clench' && <div className="w-full max-w-xl rounded-3xl bg-white p-8">
          <p className="mb-5 text-2xl">{clenched ? s.clenchWorked : s.clenchWaiting}</p>
          <Meter label={s.stepClench} value={Math.min(1, (signal?.emg ?? 0) / ((signal?.threshold ?? 1) * 2))} />
        </div>}
      </main>
      <footer className="mx-auto w-full max-w-5xl shrink-0 rounded-3xl bg-white/95 px-8 py-5 text-center shadow-lg">
        {step === 'eyes' && <div className="mb-4"><p className="mb-2 text-lg text-[#007a72]">{eyes === 'calibrating' ? s.eyesFollow : eyes === 'failed' ? s.eyesError : trackerReady ? s.calibrationIn(Math.max(0, Math.ceil((phase.until - phase.duration + CALIBRATE_AFTER_MS - now) / 1000))) : s.eyesStarting}</p><Meter label={s.calibrationProgress} value={progress} /></div>}
        <p className="mb-3 text-xl font-semibold text-[#007a72]">{input.tracked ? s.lookBlink : s.scanBlink}</p>
        <SetupButtons options={options} selected={input.selected} />
        <p className="mb-2 mt-4 text-xl tabular-nums">{s.nextIn(nextLabel, remaining)}</p>
        <Meter label={s.nextStep} value={timedProgress} />
      </footer>
    </div>
  )
}

function SetupButtons({ options, selected }: { options: SetupOption[]; selected: number }) {
  return <div className="flex flex-wrap justify-center gap-4">{options.map((option, i) => <button key={option.id} data-setup-option={i} type="button" onClick={option.act}
    className={`min-h-16 rounded-2xl px-8 py-4 text-2xl font-bold focus-visible:outline-4 focus-visible:outline-offset-4 focus-visible:outline-teal-800 ${selected === i ? 'bg-[#007a72] text-white ring-4 ring-teal-200' : 'bg-zinc-100 text-zinc-800'}`}>
    {option.label}
  </button>)}</div>
}

export function Meter({ label, value }: { label: string; value: number }) {
  const safe = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
  return <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(safe * 100)} className="h-3 w-full overflow-hidden rounded-full bg-zinc-200">
    <div className="h-full rounded-full bg-[#007a72] transition-[width] duration-150" style={{ width: `${safe * 100}%` }} />
  </div>
}
