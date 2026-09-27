import { useCallback, useEffect, useRef, useState } from 'react'
import type { Settings, Signal } from '../contracts'
import { useSocket } from '../lib/useSocket'
import { getSensor, startSensor, stopSensor, type BlinkMode, type SensorStatus } from './service'
import { museFresh, museState } from './status'

/** The main application's headband controls. Acquisition runs in sensor.main, not Tkinter. */
export function MuseInput({ compact = false }: { compact?: boolean }) {
  const [signal, setSignal] = useState<Signal | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const [service, setService] = useState<SensorStatus | null>(null)
  const [profile, setProfile] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showLog, setShowLog] = useState(false)
  const [blink, setBlink] = useState<BlinkMode>('auto')
  const chosen = useRef(false)
  const { status, send } = useSocket('/ws/console', {
    onMessage: (msg) => {
      if (msg.type === 'SIGNAL') setSignal(msg)
      if (msg.type === 'SETTINGS') setSettings(msg)
    },
  })
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 250)
    return () => window.clearInterval(timer)
  }, [])

  const apply = useCallback((next: SensorStatus) => {
    setService(next)
    // Keep the caregiver's own choice; only seed the picker the first time.
    if (!chosen.current) {
      const preferred = next.profile ?? next.profiles[0]
      if (preferred) {
        setProfile(preferred)
        chosen.current = true
      }
    }
  }, [])

  // Poll the supervisor: the subprocess can also exit on its own (no headband, BLE taken).
  useEffect(() => {
    let live = true
    const tick = () =>
      getSensor()
        .then((next) => live && apply(next))
        .catch(() => {})
    tick()
    const timer = window.setInterval(tick, 2000)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [apply])

  const act = async (run: () => Promise<SensorStatus>) => {
    setBusy(true)
    setError(null)
    try {
      apply(await run())
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const fresh = status === 'open' && museFresh(signal, now)
  const enabled = !!settings?.muse_enabled
  const state = status !== 'open' ? 'Core disconnected' : museState(signal, now, enabled)
  const running = !!service?.running
  const profiles = service?.profiles ?? []
  return (
    <details open={compact ? undefined : true} aria-label="Muse clench input" className={`rounded-xl border border-zinc-700 bg-zinc-950/95 ${compact ? 'p-2 text-sm' : 'w-full max-w-xl space-y-4 p-6'}`}>
      <summary className="cursor-pointer list-none">
        <span className="flex items-center justify-between gap-3">
        <span className="font-semibold">Muse · {signal?.profile || 'no profile'}</span>
        <span role="status" className={fresh && enabled && !signal?.blocked ? 'text-emerald-400' : 'text-amber-300'}>{state}</span>
        </span>
      </summary>

      {/* Step 1: own the headband. Without this the switch below has nothing to un-pause. */}
      <div className="mt-2 flex items-center gap-2">
        <label className="sr-only" htmlFor="muse-profile">Calibration profile</label>
        <select id="muse-profile" value={profile} disabled={running || busy || !profiles.length}
          onChange={(e) => { chosen.current = true; setProfile(e.target.value) }}
          className="min-w-0 flex-1 rounded-lg border border-zinc-700 bg-zinc-900 px-2 py-2 disabled:opacity-40">
          {profiles.length ? profiles.map((name) => <option key={name} value={name}>{name}</option>)
            : <option value="">no calibration profiles</option>}
        </select>
        <button type="button" disabled={busy || (!running && !profile)}
          onClick={() => act(running ? stopSensor : () => startSensor(profile, 'muse', blink))}
          className="rounded-lg bg-zinc-100 px-3 py-2 font-semibold text-zinc-950 disabled:opacity-40">
          {busy ? '...' : running ? 'Disconnect' : 'Connect headband'}
        </button>
      </div>
      {!running && (
        <label className="mt-2 flex items-center gap-2 text-xs text-zinc-400">
          <span className="flex-1">Double blink (back)</span>
          <select value={blink} onChange={(e) => setBlink(e.target.value as BlinkMode)} disabled={busy}
            className="rounded border border-zinc-700 bg-zinc-900 px-1 py-0.5">
            <option value="auto">from calibration</option>
            <option value="on">force on</option>
            <option value="off">off</option>
          </select>
        </label>
      )}
      {!running && (
        <button type="button" disabled={busy} onClick={() => act(() => startSensor('demo', 'demo', 'auto'))}
          className="mt-2 w-full rounded-lg border border-zinc-700 px-3 py-1.5 text-xs text-zinc-300 disabled:opacity-40">
          Run the simulated demo source instead (no headband)
        </button>
      )}
      {error && <p role="alert" className="mt-2 text-xs text-rose-400">{error}</p>}
      {running && !fresh && <p className="mt-2 text-xs text-amber-300">Connecting to the headband…</p>}

      {fresh && signal?.emg != null && signal.threshold != null && (
        <div className="mt-2">
          <meter aria-label="Clench strength" className="w-full" min={0} max={Math.max(signal.threshold * 2, signal.emg)} value={signal.emg} />
          <p className="text-xs text-zinc-400">{signal.emg.toFixed(1)} µV · threshold {signal.threshold.toFixed(1)} µV</p>
        </div>
      )}

      {/* Step 2: let clenches reach the board. Startup is always paused (PRD D5). */}
      <button type="button" disabled={!settings || status !== 'open' || (!enabled && !fresh)}
        onClick={() => settings && send({ ...settings, muse_enabled: !enabled })}
        className="mt-2 w-full rounded-lg bg-zinc-100 px-4 py-2 font-semibold text-zinc-950 disabled:opacity-40">
        {enabled ? 'Pause Muse clenches' : 'Enable Muse clenches'}
      </button>
      <p className="mt-2 text-xs text-zinc-400">Short clench: select / confirm · Hold {(settings?.long_clench_ms ?? 2500) / 1000}s: help</p>
      <p className="text-xs text-zinc-400">Double blink: go back{service?.blink === 'off' ? ' (off)' : ''} · B also goes back · press / for the input log</p>

      {!!service?.log.length && (
        <div className="mt-2">
          <button type="button" onClick={() => setShowLog(!showLog)} className="text-xs text-zinc-400 underline">
            {showLog ? 'Hide' : 'Show'} service log
          </button>
          {showLog && (
            <pre className="mt-1 max-h-32 overflow-auto rounded-lg bg-black/60 p-2 text-[10px] leading-snug text-zinc-400">
              {service.log.slice(-12).join('\n')}
            </pre>
          )}
        </div>
      )}
      {!compact && !running && <p className="text-sm text-zinc-400">Pick the wearer’s calibration profile and press Connect. Close Muse Station first so it releases the headband.</p>}
    </details>
  )
}
