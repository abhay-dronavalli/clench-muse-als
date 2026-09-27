import { useState } from 'react'
import type { CarLog, CarResult, CarState, Message, RidePhase } from '../contracts'
import { useSocket } from '../lib/useSocket'

/**
 * /car-sim: the car's side of the ride, for a second laptop. It shows the mock car's state (core/car)
 * and a live log of what crosses the car link (requests, answers, round-trip times), and it plays
 * Support: send the rider a question, put the car on the highway, change the ride's phase.
 */

const QUESTIONS: { text: string; options: string[]; urgent: boolean }[] = [
  { text: 'Are you hurt?', options: ['Yes', 'No', 'Not sure'], urgent: true },
  { text: 'Do you want to stop the ride?', options: ['Yes', 'No', 'Not sure'], urgent: false },
  { text: 'Is the temperature okay?', options: ['Yes', 'No', 'Not sure'], urgent: false },
]
const MAX_LOG = 200

export default function CarSimPage() {
  const [state, setState] = useState<CarState | null>(null)
  const [log, setLog] = useState<CarLog[]>([])
  const [last, setLast] = useState<CarResult | null>(null)
  const [custom, setCustom] = useState('')
  const { status, send } = useSocket('/ws/car-sim', {
    onOpen: (s) => s({ type: 'READY' }),
    onMessage: (msg: Message) => {
      if (msg.type === 'CAR_STATE') setState(msg)
      else if (msg.type === 'CAR_LOG') setLog((l) => [msg, ...l].slice(0, MAX_LOG))
      else if (msg.type === 'CAR_RESULT') setLast(msg)
    },
  })
  const ask = (text: string, options: string[], urgent: boolean) =>
    send({ type: 'CAR_SIM', command: 'ask', text, options, timeout_s: 30, urgent })
  const set = (patch: { on_highway?: boolean; phase?: RidePhase }) => send({ type: 'CAR_SIM', command: 'set', ...patch })

  return (
    <main className="min-h-screen bg-zinc-950 px-6 py-6 text-zinc-100">
      <div className="mx-auto flex max-w-6xl flex-col gap-6">
        <header className="flex items-baseline justify-between">
          <h1 className="text-3xl font-bold">Car simulator</h1>
          <span className={`text-sm ${status === 'open' ? 'text-green-400' : 'text-amber-400'}`}>Core link: {status}</span>
        </header>

        <section className="grid gap-4 md:grid-cols-4">
          <Stat label="Phase" value={state?.phase ?? 'unknown'} />
          <Stat label="Speed" value={state ? `${state.speed_mph} mph${state.on_highway ? ' (highway)' : ''}` : 'unknown'} />
          <Stat label="Cabin" value={state ? `${state.cabin_temp_f} °F` : 'unknown'} />
          <Stat label="Music" value={state ? `${state.music_playing === false ? 'off' : 'on'}, volume ${state.volume}` : 'unknown'} />
          <Stat
            label="Windows (% open)"
            value={state ? `FL ${state.windows.front_left} · FR ${state.windows.front_right} · RL ${state.windows.rear_left} · RR ${state.windows.rear_right}` : 'unknown'}
            wide
          />
          <Stat label="Arrival" value={state ? `${state.eta_min} min` : 'unknown'} />
          <Stat label="Battery" value={state ? `${state.battery_pct}%` : 'unknown'} />
        </section>

        <section className="flex flex-col gap-3 rounded-lg border border-zinc-800 p-4">
          <h2 className="font-semibold">Situation</h2>
          <div className="flex flex-wrap gap-2">
            <Button onClick={() => set({ on_highway: !state?.on_highway })}>{state?.on_highway ? 'Leave the highway' : 'Get on the highway'}</Button>
            <Button onClick={() => set({ phase: 'EN_ROUTE' })}>En route</Button>
            <Button onClick={() => set({ phase: 'PULLED_OVER' })}>Pulled over</Button>
            <Button onClick={() => set({ phase: 'ARRIVED' })}>Arrived</Button>
          </div>
        </section>

        <section className="flex flex-col gap-3 rounded-lg border border-zinc-800 p-4">
          <h2 className="font-semibold">Support: ask the rider</h2>
          <div className="flex flex-wrap gap-2">
            {QUESTIONS.map((q) => (
              <Button key={q.text} onClick={() => ask(q.text, q.options, q.urgent)}>
                {q.text}
                {q.urgent ? ' (urgent)' : ''}
              </Button>
            ))}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault()
              if (custom.trim()) ask(custom.trim(), ['Yes', 'No', 'Not sure'], false)
              setCustom('')
            }}
          >
            <input
              className="flex-1 rounded border border-zinc-700 bg-zinc-900 px-3 py-1.5"
              placeholder="Another yes / no question"
              value={custom}
              onChange={(e) => setCustom(e.target.value)}
            />
            <Button type="submit">Ask</Button>
          </form>
          <p className="text-xs text-zinc-500">The rider sees Yes / No / Not sure and confirms the answer. No answer in 30 s is sent as "no response".</p>
        </section>

        {last && (
          <p className="text-sm text-zinc-400">
            Last answer to the rider: <span className="text-zinc-100">{last.action_id}</span> {last.status} “{last.message}”
            {last.rtt_ms != null ? ` (${last.rtt_ms} ms)` : ''}
          </p>
        )}

        <section className="rounded-lg border border-zinc-800">
          <h2 className="border-b border-zinc-800 px-4 py-2 font-semibold">Car link log</h2>
          <div className="max-h-[50vh] overflow-y-auto">
            <table className="w-full text-left text-sm">
              <tbody>
                {log.map((e, i) => (
                  <tr key={`${e.t}-${i}`} className="border-b border-zinc-900 align-top">
                    <td className="whitespace-nowrap px-4 py-1 text-zinc-500 tabular-nums">{new Date(e.t * 1000).toLocaleTimeString()}</td>
                    <td className={`whitespace-nowrap px-2 py-1 ${e.direction === 'to_car' ? 'text-sky-300' : 'text-amber-300'}`}>
                      {e.direction === 'to_car' ? 'rider → car' : 'car → rider'}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1 text-zinc-400">{e.kind}</td>
                    <td className="px-2 py-1">{e.summary}</td>
                    <td className="whitespace-nowrap px-4 py-1 text-right text-zinc-400 tabular-nums">{e.rtt_ms != null ? `${e.rtt_ms} ms` : ''}</td>
                  </tr>
                ))}
                {log.length === 0 && (
                  <tr>
                    <td className="px-4 py-3 text-zinc-500">Nothing yet. Start a trip on the board (dev panel) and pick a control.</td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </main>
  )
}

function Stat({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={`rounded-lg bg-zinc-900 p-3 ${wide ? 'md:col-span-2' : ''}`}>
      <p className="text-xs text-zinc-400">{label}</p>
      <p className="text-lg font-semibold">{value}</p>
    </div>
  )
}

function Button({ children, onClick, type = 'button' }: { children: React.ReactNode; onClick?: () => void; type?: 'button' | 'submit' }) {
  return (
    <button type={type} onClick={onClick} className="rounded border border-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-800">
      {children}
    </button>
  )
}
