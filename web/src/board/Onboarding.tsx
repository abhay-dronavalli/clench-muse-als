import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Lang, Settings, Signal } from '../contracts'
import { nativeBridge, nativeEvents, nativeGazeActive } from '../facetrack/native'
import { useSocket } from '../lib/useSocket'
import { getSensor, startSensor } from '../sensor/service'
import { museFresh } from '../sensor/status'
import { STRINGS } from './strings'

/**
 * Getting the rider ready, right after "Click to start": a light, calm ride-style welcome, then
 *   1. the eyes: the tablet's eye tracker calibration (the shell's own five targets, ClenchNative.calibrate),
 *   2. the headband: connect the Muse with its saved profile (no Muse calibration here), wait for a live
 *      signal with every sensor touching,
 *   3. one test clench.
 * The test clench is seen in the Core's input log (INPUT_EVENT) while Muse input is still paused, so it
 * never picks anything on the board; Muse input is switched on only once the clench has worked. Every
 * step can be skipped (no tablet, no headband). Nothing here flashes: calm fades only.
 */

type Step = 'welcome' | 'eyes' | 'band' | 'clench' | 'done'
const CHANNELS = ['TP9', 'AF7', 'AF8', 'TP10'] as const
const GOOD_FOR_MS = 2000 // a clean signal this long moves on to the clench test

/** A problem the rider can fix, from the sensor's SIGNAL; null = the signal is good. */
function signalProblem(signal: Signal | null, now: number): 'none' | 'contact' | 'moving' | 'waiting' | null {
  if (!museFresh(signal, now)) return 'none'
  const blocked = signal?.blocked ?? ''
  if (/poor contact/i.test(blocked)) return 'contact'
  if (/head moving/i.test(blocked)) return 'moving'
  if (/waiting|interrupted|connecting/i.test(blocked)) return 'waiting'
  return null
}

export function Onboarding({ lang, onDone }: { lang: Lang; onDone: () => void }) {
  const s = STRINGS[lang].setup
  const [step, setStep] = useState<Step>('welcome')
  const [signal, setSignal] = useState<Signal | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const [eyes, setEyes] = useState<'idle' | 'starting' | 'ready' | 'calibrating' | 'failed' | 'error'>('idle')
  const [band, setBand] = useState<'idle' | 'connecting' | 'failed'>('idle')
  const [bandError, setBandError] = useState('')
  const [clenched, setClenched] = useState(false)
  const settings = useRef<Settings | null>(null)
  const goodSince = useRef<number | null>(null)
  const clenchFrom = useRef(0)
  const native = nativeBridge()
  const hasEyes = typeof native?.calibrate === 'function'

  const { send } = useSocket('/ws/console', {
    onMessage: (msg) => {
      if (msg.type === 'SIGNAL') setSignal(msg)
      if (msg.type === 'SETTINGS') settings.current = msg
      if (msg.type === 'INPUT_EVENT' && msg.source === 'muse' && msg.kind === 'CLENCH' && msg.t >= clenchFrom.current) {
        setClenched(true)
      }
    },
  })
  const setting = useCallback(
    (extra: Partial<Settings>) => {
      const cur = settings.current
      if (cur) send({ type: 'SETTINGS', pointing_mode: cur.pointing_mode, scan_ms: cur.scan_ms, ...extra })
    },
    [send],
  )

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 250)
    return () => window.clearInterval(timer)
  }, [])

  // The shell's eye tracker and its calibration report back through native events.
  useEffect(
    () =>
      nativeEvents.subscribe((e) => {
        if (e.type === 'tracker') {
          if (e.state === 'on') setEyes((v) => (v === 'calibrating' ? v : 'ready'))
          if (e.state === 'error') setEyes('error')
        }
        if (e.type === 'calibration') {
          if (e.state === 'finished') setStep('band')
          if (e.state === 'canceled') setEyes('failed')
        }
      }),
    [],
  )

  // Eyes: make sure the tracker runs (Auto pointing), then the rider starts the calibration.
  const enterEyes = () => {
    setStep('eyes')
    if (!hasEyes) return
    if (nativeGazeActive()) setEyes('ready')
    else {
      setEyes('starting')
      setting({ pointing_mode: 'auto' })
    }
  }
  const calibrate = () => {
    setEyes('calibrating')
    native?.calibrate('patient')
  }

  // Headband: paused input while testing, then connect with the saved profile.
  const connect = async () => {
    setBand('connecting')
    setBandError('')
    setting({ muse_enabled: false })
    try {
      const status = await getSensor()
      if (!status.running) {
        const profile = status.profile && status.profiles.includes(status.profile) ? status.profile : status.profiles[0]
        if (!profile) throw new Error(s.noProfile)
        await startSensor(profile, 'muse')
      }
    } catch (e) {
      setBand('failed')
      setBandError(e instanceof Error ? e.message : String(e))
    }
  }
  const enterBand = () => {
    setStep('band')
    void connect()
  }

  // A clean signal for a moment: on to the clench.
  const problem = signalProblem(signal, now)
  useEffect(() => {
    if (step !== 'band') return
    if (problem !== null) {
      goodSince.current = null
      return
    }
    goodSince.current ??= Date.now()
    if (Date.now() - goodSince.current >= GOOD_FOR_MS) {
      clenchFrom.current = Date.now() / 1000
      setClenched(false)
      setStep('clench')
    }
  }, [step, problem, now])

  const finish = (museOn: boolean) => {
    if (museOn) setting({ muse_enabled: true })
    setStep('done')
  }

  const jaw = signal?.emg ?? 0
  const threshold = signal?.threshold ?? 1
  const channels = museFresh(signal, now) ? signal?.ch ?? [] : []

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-[linear-gradient(160deg,#eaf6f4_0%,#f6f8f8_45%,#e3eef7_100%)] p-10 text-zinc-900">
      <div className="flex w-full max-w-5xl flex-col items-center rounded-[2.5rem] bg-white px-16 py-12 shadow-2xl shadow-black/10">
        <p className="text-lg font-bold uppercase tracking-[0.2em] text-[#007a72]">{s.brand}</p>
        <Steps step={step} lang={lang} />

        {step === 'welcome' && (
          <Body icon={<CarIcon />} title={s.welcomeTitle} text={s.welcomeText}>
            <Primary onClick={enterEyes}>{s.begin}</Primary>
          </Body>
        )}

        {step === 'eyes' && (
          <Body icon={<EyeIcon />} title={s.eyesTitle} text={hasEyes ? s.eyesText : s.eyesNone}>
            {!hasEyes && <Primary onClick={enterBand}>{s.next}</Primary>}
            {hasEyes && eyes === 'starting' && <Status>{s.eyesStarting}</Status>}
            {hasEyes && eyes === 'error' && <Status tone="warn">{s.eyesError}</Status>}
            {hasEyes && (eyes === 'ready' || eyes === 'failed' || eyes === 'starting') && (
              <Primary onClick={calibrate} disabled={eyes === 'starting'}>
                {eyes === 'failed' ? s.tryAgain : s.eyesStart}
              </Primary>
            )}
            {hasEyes && eyes === 'calibrating' && <Status>{s.eyesFollow}</Status>}
            <Skip onClick={enterBand}>{s.skip}</Skip>
          </Body>
        )}

        {step === 'band' && (
          <Body icon={<BandIcon />} title={s.bandTitle} text={s.bandText}>
            <Channels channels={channels} />
            <Status tone={problem === null ? 'good' : problem === 'contact' || band === 'failed' ? 'warn' : undefined}>
              {band === 'failed'
                ? `${s.bandFailed} ${bandError}`
                : problem === 'none'
                  ? s.bandLooking
                  : problem === 'contact'
                    ? s.bandContact
                    : problem === 'moving'
                      ? s.bandStill
                      : problem === 'waiting'
                        ? s.bandWaiting
                        : s.bandGood}
            </Status>
            {band === 'failed' && <Primary onClick={() => void connect()}>{s.tryAgain}</Primary>}
            <Skip onClick={() => finish(false)}>{s.skipBand}</Skip>
          </Body>
        )}

        {step === 'clench' && (
          <Body icon={<JawIcon />} title={s.clenchTitle} text={clenched ? s.clenchWorked : s.clenchText}>
            <JawMeter level={jaw} threshold={threshold} />
            {clenched ? (
              <Primary onClick={() => finish(true)}>{s.finish}</Primary>
            ) : (
              <Status>{problem === 'moving' ? s.bandStill : s.clenchWaiting}</Status>
            )}
            <Skip onClick={() => finish(false)}>{s.skipBand}</Skip>
          </Body>
        )}

        {step === 'done' && (
          <Body icon={<CheckIcon />} title={s.doneTitle} text={s.doneText}>
            <Primary onClick={onDone}>{s.go}</Primary>
          </Body>
        )}
      </div>
    </div>
  )
}

function Steps({ step, lang }: { step: Step; lang: Lang }) {
  const s = STRINGS[lang].setup
  const order: Step[] = ['eyes', 'band', 'clench']
  const at = step === 'welcome' ? -1 : step === 'done' ? 3 : order.indexOf(step)
  return (
    <div className="mt-6 flex items-center gap-4">
      {[s.stepEyes, s.stepBand, s.stepClench].map((label, i) => (
        <div key={label} className="flex items-center gap-4">
          <span
            className={`flex items-center gap-2 rounded-full px-4 py-1.5 text-lg transition-colors duration-500 ${
              i < at ? 'bg-[#e0f4f2] text-[#007a72]' : i === at ? 'bg-[#00a99d] font-semibold text-white' : 'bg-zinc-100 text-zinc-500'
            }`}
          >
            {i < at ? '✓' : i + 1} {label}
          </span>
          {i < 2 && <span className="h-0.5 w-8 bg-zinc-200" />}
        </div>
      ))}
    </div>
  )
}

function Body({ icon, title, text, children }: { icon: ReactNode; title: string; text: string; children: ReactNode }) {
  return (
    <div className="mt-10 flex flex-col items-center text-center">
      <div className="flex h-28 w-28 items-center justify-center rounded-full bg-[#e0f4f2] text-[#00a99d]">{icon}</div>
      <h1 className="mt-8 text-6xl font-bold tracking-tight">{title}</h1>
      <p className="mt-5 max-w-3xl text-3xl leading-snug text-zinc-600">{text}</p>
      <div className="mt-10 flex flex-col items-center gap-5">{children}</div>
    </div>
  )
}

function Primary({ onClick, disabled, children }: { onClick: () => void; disabled?: boolean; children: ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="min-w-80 rounded-3xl bg-[#00a99d] px-14 py-6 text-4xl font-bold text-white shadow-lg shadow-[#00a99d]/30 transition-opacity disabled:opacity-40"
    >
      {children}
    </button>
  )
}

function Skip({ onClick, children }: { onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className="text-2xl text-zinc-500 underline-offset-4 hover:underline">
      {children}
    </button>
  )
}

function Status({ tone, children }: { tone?: 'good' | 'warn'; children: ReactNode }) {
  const colour = tone === 'good' ? 'text-[#007a72]' : tone === 'warn' ? 'text-amber-700' : 'text-zinc-600'
  return <p className={`text-3xl font-semibold ${colour}`}>{children}</p>
}

/** Each sensor: touching (teal), too flat or too noisy (amber), or no data yet (grey). */
function Channels({ channels }: { channels: number[] }) {
  return (
    <div className="flex gap-4">
      {CHANNELS.map((name, i) => {
        const std = channels[i]
        const ok = std !== undefined && std >= 1 && std <= 200
        const tone = std === undefined ? 'bg-zinc-100 text-zinc-400' : ok ? 'bg-[#e0f4f2] text-[#007a72]' : 'bg-amber-100 text-amber-800'
        return (
          <span key={name} className={`rounded-2xl px-5 py-3 text-2xl font-semibold transition-colors duration-500 ${tone}`}>
            {name}
          </span>
        )
      })}
    </div>
  )
}

/** The jaw's muscle level against the calibrated threshold (the line in the middle). */
function JawMeter({ level, threshold }: { level: number; threshold: number }) {
  const share = Math.min(1, level / (threshold * 2))
  return (
    <div className="relative h-6 w-[36rem] overflow-hidden rounded-full bg-zinc-100">
      <div
        className={`h-full rounded-full transition-[width] duration-200 ${level >= threshold ? 'bg-[#00a99d]' : 'bg-sky-400'}`}
        style={{ width: `${share * 100}%` }}
      />
      <div className="absolute inset-y-0 left-1/2 w-1 bg-zinc-400" />
    </div>
  )
}

const iconProps = { width: 60, height: 60, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const }

function CarIcon() {
  return (
    <svg {...iconProps}>
      <path d="M3 13l2-5a2 2 0 0 1 1.9-1.3h10.2A2 2 0 0 1 19 8l2 5v4a1 1 0 0 1-1 1h-1.5M3 13v4a1 1 0 0 0 1 1h1.5M3 13h18" />
      <circle cx="7.5" cy="17.5" r="1.8" />
      <circle cx="16.5" cy="17.5" r="1.8" />
    </svg>
  )
}
function EyeIcon() {
  return (
    <svg {...iconProps}>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  )
}
function BandIcon() {
  return (
    <svg {...iconProps}>
      <path d="M4 15a8 8 0 0 1 16 0" />
      <path d="M4 15v2M20 15v2M9 8.5l.5 1.5M15 8.5l-.5 1.5" />
    </svg>
  )
}
function JawIcon() {
  return (
    <svg {...iconProps}>
      <path d="M6 9c0-3 2.7-5 6-5s6 2 6 5v2c0 4-3 8-6 8s-6-4-6-8V9z" />
      <path d="M9 14h6" />
    </svg>
  )
}
function CheckIcon() {
  return (
    <svg {...iconProps}>
      <path d="M5 12.5l4.5 4.5L19 7.5" />
    </svg>
  )
}
