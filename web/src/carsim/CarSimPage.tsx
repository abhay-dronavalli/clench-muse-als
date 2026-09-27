import { Battery, Car, CircleHelp, Clock, Gauge, Music, Play, Square, Thermometer, Volume2, Wind } from 'lucide-react'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import type { CarLog, CarResult, CarState, Message, RidePhase } from '../contracts'
import { useSocket } from '../lib/useSocket'

/**
 * /car-sim: the car's side of the ride, for a second laptop. It shows the mock car's state (core/car)
 * and a live log of what crosses the car link (requests, answers, round-trip times), and it plays
 * Support: send the rider a question, put the car on the highway, change the ride's phase.
 * Styled like Car mode: the light-blue backdrop, white rounded cards, sky-700 actions, amber highlight.
 */

const QUESTIONS: { text: string; options: string[]; urgent: boolean }[] = [
  { text: 'Are you hurt?', options: ['Yes', 'No', 'Not sure'], urgent: true },
  { text: 'Do you want to stop the ride?', options: ['Yes', 'No', 'Not sure'], urgent: false },
  { text: 'Is the temperature okay?', options: ['Yes', 'No', 'Not sure'], urgent: false },
]
const QUESTION_TIMEOUT_S = 30
const MAX_LOG = 200
// The typed-address planner is hidden for the demo: the board uses the saved places' committed trips.
const SHOW_ADDRESS_BOX = false

const PHASES: { id: RidePhase; label: string }[] = [
  { id: 'BOARDING', label: 'Parked' },
  { id: 'EN_ROUTE', label: 'En route' },
  { id: 'PULLED_OVER', label: 'Pulled over' },
  { id: 'ARRIVED', label: 'Arrived' },
]
const STATUS_CHIP: Record<string, string> = {
  ACCEPTED: 'bg-sky-100 text-sky-800',
  COMPLETED: 'bg-emerald-100 text-emerald-800',
  DELAYED: 'bg-amber-100 text-amber-800',
  REJECTED: 'bg-red-100 text-red-800',
}

export default function CarSimPage() {
  const [state, setState] = useState<CarState | null>(null)
  const [log, setLog] = useState<CarLog[]>([])
  const [last, setLast] = useState<CarResult | null>(null)
  const [custom, setCustom] = useState('')
  const [address, setAddress] = useState('')
  const [now, setNow] = useState(() => Date.now() / 1000)
  // The car's speakers: a short loop plays while the car reports music on (browsers need one click
  // on this page before any sound).
  const [speakers, setSpeakers] = useState(false)
  const audio = useRef<HTMLAudioElement | null>(null)
  const musicOn = speakers && state?.music_playing === true && state.phase !== 'ARRIVED'
  useEffect(() => {
    const a = (audio.current ??= Object.assign(new Audio('/carsim/music-loop.wav'), { loop: true }))
    a.volume = Math.min(1, Math.max(0, (state?.volume ?? 4) / 10))
    if (musicOn) void a.play().catch(() => setSpeakers(false))
    else a.pause()
  }, [musicOn, state?.volume])
  useEffect(() => () => audio.current?.pause(), [])
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 500)
    return () => window.clearInterval(timer)
  }, [])
  const { status, send } = useSocket('/ws/car-sim', {
    onOpen: (s) => s({ type: 'READY' }),
    onMessage: (msg: Message) => {
      if (msg.type === 'CAR_STATE') setState(msg)
      else if (msg.type === 'CAR_LOG') setLog((l) => [msg, ...l].slice(0, MAX_LOG))
      else if (msg.type === 'CAR_RESULT') setLast(msg)
    },
  })
  const ask = (text: string, options: string[], urgent: boolean) =>
    send({ type: 'CAR_SIM', command: 'ask', text, options, timeout_s: QUESTION_TIMEOUT_S, urgent })
  const set = (patch: { on_highway?: boolean; phase?: RidePhase }) => send({ type: 'CAR_SIM', command: 'set', ...patch })

  // The question the rider has not answered yet (from the log): its countdown until no_response.
  const lastAsk = log.find((e) => e.kind === 'SupportQuestion')
  const answered = lastAsk && log.some((e) => e.kind === 'SupportAnswer' && e.request_id === lastAsk.request_id)
  const pendingLeft = lastAsk && !answered ? Math.ceil(lastAsk.t + QUESTION_TIMEOUT_S - now) : 0
  const pending = lastAsk && !answered && pendingLeft > 0 ? lastAsk : null
  const online = status === 'open'
  const phase = PHASES.find((p) => p.id === state?.phase)

  return (
    <main className="min-h-screen bg-[linear-gradient(to_bottom,#b9d9f4_0%,#e4f1fb_40%,#f6f8f8_100%)] px-6 py-6 text-zinc-900">
      <div className="mx-auto flex max-w-6xl flex-col gap-5">
        <header className="flex flex-wrap items-center justify-between gap-3 rounded-3xl bg-white/95 px-6 py-4 shadow-sm">
          <div className="flex items-center gap-3">
            <Car className="h-9 w-9 text-sky-700" aria-hidden />
            <h1 className="text-4xl font-bold">Car</h1>
          </div>
          <div className="flex items-center gap-3">
            <span className="rounded-full bg-zinc-900 px-4 py-1.5 text-lg font-semibold text-white">{phase?.label ?? 'No ride'}</span>
            <span className={`flex items-center gap-2 rounded-full px-4 py-1.5 text-lg font-semibold ${online ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}`}>
              <span className={`h-2.5 w-2.5 rounded-full ${online ? 'bg-emerald-500' : 'bg-red-500'}`} />
              Core link: {online ? 'connected' : 'offline'}
            </span>
          </div>
        </header>

        <ol className="grid grid-cols-4 gap-2 rounded-3xl bg-white/95 p-3 shadow-sm">
          {PHASES.map((p) => {
            const on = p.id === state?.phase
            return (
              <li key={p.id} className={`rounded-2xl px-3 py-3 text-center text-lg font-semibold ${on ? 'bg-zinc-900 text-white ring-4 ring-amber-500' : 'bg-zinc-100 text-zinc-500'}`}>
                {p.label}
              </li>
            )
          })}
        </ol>

        <section className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat icon={<Gauge />} label="Speed" value={state ? `${state.speed_mph}` : '–'} unit={state ? `mph${state.on_highway ? ' · highway' : ''}` : ''} />
          <Stat icon={<Thermometer />} label="Cabin" value={state ? `${state.cabin_temp_f}` : '–'} unit="°F" />
          <Stat icon={<Music />} label="Music" value={state ? (state.music_playing === false ? 'Off' : 'On') : '–'} unit={state ? `volume ${state.volume}` : ''}
            badge={musicOn ? <Playing /> : null} />
          <Stat icon={<Clock />} label="Arrival" value={state ? `${state.eta_min}` : '–'} unit="min" />
          <Stat icon={<Wind />} label="Windows (% open)" wide
            value={state ? `${state.windows.front_left} · ${state.windows.front_right} · ${state.windows.rear_left} · ${state.windows.rear_right}` : '–'}
            unit="FL · FR · RL · RR" />
          <Stat icon={<Battery />} label="Battery" value={state ? `${state.battery_pct}` : '–'} unit="%" />
          <Stat icon={<Volume2 />} label="Speakers" value={speakers ? 'On' : 'Off'} unit={musicOn ? 'playing' : speakers ? 'waiting for music' : 'click to allow sound'} />
        </section>

        <section className="flex flex-col gap-3 rounded-3xl bg-white/95 p-5 shadow-sm">
          <h2 className="text-xl font-bold">Ride</h2>
          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={() => send({ type: 'CAR_SIM', command: 'start_ride' })}
              className="flex items-center gap-2 rounded-2xl bg-sky-700 px-6 py-4 text-xl font-bold text-white shadow-sm hover:bg-sky-800">
              <Play className="h-6 w-6" aria-hidden /> Start ride
            </button>
            <button type="button" onClick={() => send({ type: 'CAR_SIM', command: 'end_ride' })}
              className="flex items-center gap-2 rounded-2xl bg-white px-6 py-4 text-xl font-bold text-zinc-800 ring-2 ring-zinc-300 hover:bg-zinc-50">
              <Square className="h-5 w-5" aria-hidden /> End ride
            </button>
            <button type="button" onClick={() => setSpeakers((v) => !v)} aria-pressed={speakers}
              className={`flex items-center gap-2 rounded-2xl px-6 py-4 text-xl font-bold ${speakers ? 'bg-zinc-900 text-white ring-4 ring-amber-500' : 'bg-zinc-100 text-zinc-800 ring-1 ring-black/10'}`}>
              <Volume2 className="h-6 w-6" aria-hidden /> Car speakers: {speakers ? 'on' : 'off'}
            </button>
          </div>
          <div className="flex flex-wrap gap-2 pt-1">
            <Small onClick={() => set({ on_highway: !state?.on_highway })}>{state?.on_highway ? 'Leave the highway' : 'Get on the highway'}</Small>
            <Small onClick={() => set({ phase: 'EN_ROUTE' })}>En route</Small>
            <Small onClick={() => set({ phase: 'PULLED_OVER' })}>Pulled over</Small>
            <Small onClick={() => set({ phase: 'ARRIVED' })}>Arrived</Small>
          </div>
        </section>

        <section className="flex flex-col gap-3 rounded-3xl bg-white/95 p-5 shadow-sm">
          <h2 className="flex items-center gap-2 text-xl font-bold"><CircleHelp className="h-6 w-6 text-sky-700" aria-hidden /> Support: ask the rider</h2>
          {pending && (
            <div className="flex items-center justify-between rounded-2xl bg-amber-50 px-5 py-4 ring-2 ring-amber-400">
              <span className="text-2xl font-semibold">Waiting for the rider: {pending.summary.split(' [')[0]}</span>
              <span className="text-3xl font-bold tabular-nums text-amber-700">{pendingLeft}s</span>
            </div>
          )}
          <div className="grid gap-3 md:grid-cols-3">
            {QUESTIONS.map((q) => (
              <button key={q.text} type="button" onClick={() => ask(q.text, q.options, q.urgent)}
                className={`rounded-2xl px-5 py-5 text-left text-2xl font-bold shadow-sm ${q.urgent ? 'bg-red-50 text-red-900 ring-2 ring-red-300' : 'bg-white ring-2 ring-zinc-200 hover:bg-zinc-50'}`}>
                {q.text}
                {q.urgent && <span className="mt-1 block text-base font-semibold text-red-700">urgent</span>}
              </button>
            ))}
          </div>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (custom.trim()) ask(custom.trim(), ['Yes', 'No', 'Not sure'], false); setCustom('') }}>
            <input className="flex-1 rounded-2xl bg-white px-4 py-3 text-lg ring-2 ring-zinc-200 focus:outline-none focus:ring-sky-500"
              placeholder="Another yes / no question" value={custom} onChange={(e) => setCustom(e.target.value)} />
            <button type="submit" className="rounded-2xl bg-sky-700 px-6 py-3 text-lg font-bold text-white">Ask</button>
          </form>
          <p className="text-sm text-zinc-500">The rider sees Yes / No / Not sure and confirms the answer. No answer in {QUESTION_TIMEOUT_S} s is sent as "no response".</p>
        </section>

        {SHOW_ADDRESS_BOX && (
          <section className="flex flex-col gap-3 rounded-3xl bg-white/95 p-5 shadow-sm">
            <h2 className="text-xl font-bold">Plan a trip to any address</h2>
            <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (address.trim()) send({ type: 'CAR_SIM', command: 'plan', text: address.trim() }) }}>
              <input className="flex-1 rounded-2xl bg-white px-4 py-3 text-lg ring-2 ring-zinc-200" value={address} onChange={(e) => setAddress(e.target.value)} />
              <button type="submit" className="rounded-2xl bg-sky-700 px-6 py-3 text-lg font-bold text-white">Plan</button>
            </form>
          </section>
        )}

        {last && (
          <p className="text-base text-zinc-600">
            Last answer to the rider: <span className="font-semibold text-zinc-900">{last.action_id}</span>{' '}
            <Chip status={last.status} /> “{last.message}”{last.rtt_ms != null ? ` · ${last.rtt_ms} ms` : ''}
          </p>
        )}

        <section className="rounded-3xl bg-white/95 shadow-sm">
          <h2 className="border-b border-zinc-100 px-5 py-3 text-xl font-bold">Car link</h2>
          <ol className="max-h-[50vh] divide-y divide-zinc-100 overflow-y-auto">
            {log.map((e, i) => {
              const status = /: (ACCEPTED|COMPLETED|DELAYED|REJECTED)\b/.exec(e.summary)?.[1]
              const toCar = e.direction === 'to_car'
              return (
                <li key={`${e.t}-${i}`} className="flex items-start gap-3 px-5 py-2.5">
                  <span className="w-20 shrink-0 pt-0.5 text-sm tabular-nums text-zinc-400">{new Date(e.t * 1000).toLocaleTimeString()}</span>
                  <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${toCar ? 'bg-sky-500' : 'bg-amber-500'}`} aria-hidden />
                  <span className="w-28 shrink-0 text-sm font-semibold text-zinc-500">{toCar ? 'Rider → car' : 'Car → rider'}</span>
                  <span className="flex-1 text-base">
                    <span className="mr-2 text-sm text-zinc-400">{e.kind}</span>
                    {status && <Chip status={status} />} {status ? e.summary.split(`: ${status}`)[1]?.replace(/^,\s*/, '') : e.summary}
                  </span>
                  {e.rtt_ms != null && <span className="shrink-0 rounded-full bg-zinc-100 px-2.5 py-0.5 text-sm tabular-nums text-zinc-600">{e.rtt_ms} ms</span>}
                </li>
              )
            })}
            {log.length === 0 && <li className="px-5 py-4 text-zinc-500">Nothing yet. Start a ride, then pick a control on the tablet.</li>}
          </ol>
        </section>
      </div>
    </main>
  )
}

function Stat({ icon, label, value, unit, wide, badge }: { icon: ReactNode; label: string; value: string; unit?: string; wide?: boolean; badge?: ReactNode }) {
  return (
    <div className={`flex flex-col gap-1 rounded-3xl bg-white/95 p-4 shadow-sm ${wide ? 'col-span-2' : ''}`}>
      <div className="flex items-center justify-between text-sky-700">
        <span className="flex items-center gap-2 text-sm font-semibold text-zinc-500 [&>svg]:h-5 [&>svg]:w-5 [&>svg]:text-sky-700">{icon}{label}</span>
        {badge}
      </div>
      <p className="text-4xl font-bold tabular-nums">{value}</p>
      {unit && <p className="text-sm text-zinc-500">{unit}</p>}
    </div>
  )
}

function Playing() {
  return (
    <span className="flex h-4 items-end gap-0.5" aria-label="playing">
      {[0, 150, 300].map((d) => <span key={d} className="w-1 animate-pulse rounded bg-sky-600" style={{ height: '100%', animationDelay: `${d}ms` }} />)}
    </span>
  )
}

function Chip({ status }: { status: string }) {
  return <span className={`rounded-full px-2.5 py-0.5 text-sm font-semibold ${STATUS_CHIP[status] ?? 'bg-zinc-100 text-zinc-700'}`}>{status.charAt(0) + status.slice(1).toLowerCase()}</span>
}

function Small({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return (
    <button type="button" onClick={onClick} className="rounded-2xl bg-zinc-100 px-4 py-2 text-base font-semibold text-zinc-700 ring-1 ring-black/10 hover:bg-zinc-200">
      {children}
    </button>
  )
}
