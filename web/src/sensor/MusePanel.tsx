import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { Settings, Signal } from '../contracts'
import { useSocket } from '../lib/useSocket'
import { getSensor, startSensor, stopSensor, type BlinkMode, type SensorStatus } from './service'
import { museFresh, museState } from './status'

/**
 * Muse debug panel: a full-height strip on the left, toggled with "," (Esc closes). It holds every
 * headband control and readout in one place, top to bottom in the order you need them:
 *   1. the Sensor Service (profile, double blink, Connect / Disconnect, demo source),
 *   2. the live signal (per-channel spread, jaw level against the calibrated threshold, sample age),
 *   3. the Enable / Pause switch that lets gestures reach the board,
 *   4. the service's own output, always visible, so a Bluetooth failure is never hidden.
 * Closed, it leaves only a small tab on the left edge showing the headband state.
 *
 * Acquisition runs in sensor.main (started through /api/sensor), never in the browser.
 */

const CHANNELS = ['TP9', 'AF7', 'AF8', 'TP10'] as const
// Channel spread (std, uV) outside this range is a flat (not touching) or a noisy electrode.
const FLAT_UV = 1
const NOISY_UV = 200

function contact(std: number): { label: string; tone: string } {
  if (std < FLAT_UV) return { label: 'flat', tone: 'text-rose-400' }
  if (std > NOISY_UV) return { label: 'noisy', tone: 'text-amber-300' }
  return { label: 'ok', tone: 'text-emerald-400' }
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="border-b border-zinc-800 px-3 py-3">
      <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">{title}</h3>
      {children}
    </section>
  )
}

export function MusePanel({ defaultOpen = false }: { defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const [signal, setSignal] = useState<Signal | null>(null)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [now, setNow] = useState(() => Date.now() / 1000)
  const [service, setService] = useState<SensorStatus | null>(null)
  const [profile, setProfile] = useState('')
  const [blink, setBlink] = useState<BlinkMode>('auto')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const chosen = useRef(false)
  const logEnd = useRef<HTMLSpanElement | null>(null)

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

  // "," toggles the panel, Esc closes it; ignored while typing in a field.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return
      if (e.key === ',') {
        e.preventDefault()
        setOpen((was) => !was)
      } else if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const apply = useCallback((next: SensorStatus) => {
    setService(next)
    if (!chosen.current) {
      const preferred = next.profile && next.profiles.includes(next.profile) ? next.profile : next.profiles[0]
      if (preferred) {
        setProfile(preferred)
        chosen.current = true
      }
    }
  }, [])

  // Poll the supervisor: the subprocess can also exit on its own.
  useEffect(() => {
    let live = true
    const tick = () => getSensor().then((next) => live && apply(next)).catch(() => {})
    tick()
    const timer = window.setInterval(tick, 1500)
    return () => {
      live = false
      window.clearInterval(timer)
    }
  }, [apply])

  useEffect(() => {
    if (open) logEnd.current?.scrollIntoView({ block: 'end' })
  }, [open, service?.log.length])

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

  const coreUp = status === 'open'
  const fresh = coreUp && museFresh(signal, now)
  const enabled = !!settings?.muse_enabled
  const running = !!service?.running
  const state = !coreUp ? 'Core disconnected' : !running && !fresh ? 'Service stopped' : museState(signal, now, enabled)
  const good = fresh && enabled && !signal?.blocked
  const tone = good ? 'bg-emerald-400' : fresh ? 'bg-amber-300' : 'bg-rose-500'
  const age = signal ? Math.max(0, now - signal.t) : null
  const profiles = service?.profiles ?? []

  if (!open) {
    return (
      <button type="button" onClick={() => setOpen(true)} title="Muse debug panel (,)"
        className="fixed left-0 top-1/2 z-40 flex -translate-y-1/2 items-center gap-1.5 rounded-r-lg border border-l-0 border-zinc-700 bg-zinc-950/90 px-2 py-1.5 text-xs text-zinc-300">
        <span className={`h-2 w-2 rounded-full ${tone}`} />
        Muse <span className="text-zinc-500">,</span>
      </button>
    )
  }

  return (
    <aside aria-label="Muse debug panel"
      className="fixed inset-y-0 left-0 z-40 flex w-80 max-w-[90vw] flex-col border-r border-zinc-700 bg-zinc-950/95 text-sm text-zinc-200 shadow-2xl backdrop-blur">
      <header className="flex items-center justify-between border-b border-zinc-800 px-3 py-2">
        <span className="flex items-center gap-2 font-semibold">
          <span className={`h-2.5 w-2.5 rounded-full ${tone}`} /> Muse
        </span>
        <span role="status" className={`text-xs ${good ? 'text-emerald-400' : 'text-amber-300'}`}>{state}</span>
        <button type="button" onClick={() => setOpen(false)} className="text-xs text-zinc-500 hover:text-zinc-300">, close</button>
      </header>

      <div className="flex-1 overflow-y-auto">
        <Section title="1 · Headband">
          <div className="grid grid-cols-[auto_1fr] items-center gap-x-2 gap-y-1.5 text-xs">
            <label htmlFor="muse-profile" className="text-zinc-400">Profile</label>
            <select id="muse-profile" value={profile} disabled={running || busy || !profiles.length}
              onChange={(e) => { chosen.current = true; setProfile(e.target.value) }}
              className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 disabled:opacity-50">
              {profiles.length ? profiles.map((name) => <option key={name} value={name}>{name}</option>)
                : <option value="">no calibration profiles</option>}
            </select>
            <label htmlFor="muse-blink" className="text-zinc-400">Double blink</label>
            <select id="muse-blink" value={running ? (service?.blink ?? blink) : blink} disabled={running || busy}
              onChange={(e) => setBlink(e.target.value as BlinkMode)}
              className="rounded border border-zinc-700 bg-zinc-900 px-1.5 py-1 disabled:opacity-50">
              <option value="auto">from calibration</option>
              <option value="on">force on</option>
              <option value="off">off</option>
            </select>
            <span className="text-zinc-400">Service</span>
            <span className={running ? 'text-emerald-400' : 'text-zinc-500'}>
              {running ? `running (${service?.source}, pid ${service?.pid})` : service?.exit_code != null ? `exited (${service.exit_code})` : 'stopped'}
            </span>
          </div>
          <button type="button" disabled={busy || (!running && !profile)}
            onClick={() => act(running ? stopSensor : () => startSensor(profile, 'muse', blink))}
            className={`mt-2 w-full rounded-lg px-3 py-2 font-semibold disabled:opacity-40 ${running ? 'border border-zinc-600 text-zinc-200' : 'bg-zinc-100 text-zinc-950'}`}>
            {busy ? '…' : running ? 'Disconnect' : 'Connect headband'}
          </button>
          {!running && (
            <button type="button" disabled={busy} onClick={() => act(() => startSensor('demo', 'demo', 'auto'))}
              className="mt-1.5 w-full rounded-lg border border-zinc-800 px-3 py-1 text-xs text-zinc-400 disabled:opacity-40">
              Run the simulated demo source (no headband)
            </button>
          )}
          {error && <p role="alert" className="mt-2 text-xs text-rose-400">{error}</p>}
        </Section>

        <Section title="2 · Live signal">
          {!fresh ? (
            <p className="text-xs text-zinc-500">
              {running ? 'Searching for the headband… (see the output below)' : 'No signal. Connect the headband.'}
            </p>
          ) : (
            <>
              <table className="w-full text-xs tabular-nums">
                <tbody>
                  {CHANNELS.map((name, i) => {
                    const std = signal?.ch[i]
                    const c = std == null ? null : contact(std)
                    return (
                      <tr key={name}>
                        <td className="w-12 text-zinc-400">{name}</td>
                        <td className="text-right">{std == null ? '—' : std.toFixed(1)}</td>
                        <td className="w-8 pl-1 text-zinc-600">µV</td>
                        <td className={`w-12 text-right ${c?.tone ?? 'text-zinc-600'}`}>{c?.label ?? ''}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
              {signal?.emg != null && signal.threshold != null && (
                <div className="mt-2">
                  <div className="flex justify-between text-xs text-zinc-400">
                    <span>Jaw</span>
                    <span className="tabular-nums">{signal.emg.toFixed(1)} / {signal.threshold.toFixed(1)} µV</span>
                  </div>
                  <div className="relative mt-1 h-2 rounded bg-zinc-800">
                    <div className={`h-2 rounded ${signal.emg >= signal.threshold ? 'bg-emerald-400' : 'bg-sky-500'}`}
                      style={{ width: `${Math.min(100, (signal.emg / (signal.threshold * 2)) * 100)}%` }} />
                    <div className="absolute inset-y-[-2px] left-1/2 w-px bg-zinc-300" title="threshold" />
                  </div>
                </div>
              )}
              <p className="mt-2 text-xs text-zinc-500">
                {signal?.blocked ? <span className="text-amber-300">{signal.blocked}</span> : 'Clear'}
                {age != null && <span className="float-right tabular-nums">{Math.round(age * 1000)} ms old</span>}
              </p>
            </>
          )}
        </Section>

        <Section title="3 · Input">
          <button type="button" disabled={!settings || !coreUp || (!enabled && !fresh)}
            onClick={() => settings && send({ ...settings, muse_enabled: !enabled })}
            className={`w-full rounded-lg px-3 py-2 font-semibold disabled:opacity-40 ${enabled ? 'border border-zinc-600 text-zinc-200' : 'bg-emerald-400 text-zinc-950'}`}>
            {enabled ? 'Pause Muse input' : 'Enable Muse input'}
          </button>
          <ul className="mt-2 space-y-0.5 text-xs text-zinc-400">
            <li>Clench: select / confirm</li>
            <li>Hold {(settings?.long_clench_ms ?? 2500) / 1000} s: help</li>
            <li>Double blink: back{service?.blink === 'off' ? ' (off)' : ''} · B also goes back</li>
            <li className="text-zinc-500">Press / for the input log (shows ignored gestures too)</li>
          </ul>
        </Section>

        <Section title="4 · Service output">
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap break-all rounded bg-black/60 p-2 font-mono text-[10px] leading-snug text-zinc-400">
            {service?.log.length ? service.log.slice(-30).join('\n') : 'Nothing yet.'}
            <span ref={logEnd} />
          </pre>
        </Section>
      </div>
    </aside>
  )
}
