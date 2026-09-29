import { useCallback, useEffect, useMemo, useState } from 'react'
import { TripMap, type MapPin } from './TripMap'
import { calmingText, factorText, km, minutes } from './format'
import type { Candidate, RouteStats, Trip } from './types'

async function getTrip(refresh: boolean): Promise<Trip> {
  const res = await fetch(`/api/geo/trip${refresh ? '?refresh=true' : ''}`)
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`)
  return body as Trip
}

/** /trip: the demo trip from open map data. Ride comfort routes (Layer 1) and accessible drop-off
 * points (Layer 2). Every number comes from GET /api/geo/trip; nothing here is computed from Google. */
export default function TripPage() {
  const [trip, setTrip] = useState<Trip | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(true)
  const [selected, setSelected] = useState<string | undefined>()

  const show = useCallback((t: Trip) => {
    setTrip(t)
    setSelected(t.dropoff?.candidates[0]?.id)
    setError(null)
  }, [])
  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), [])

  useEffect(() => {
    getTrip(false)
      .then(show, fail)
      .finally(() => setBusy(false))
  }, [show, fail])

  const load = (refresh: boolean) => {
    setBusy(true)
    getTrip(refresh)
      .then(show, fail)
      .finally(() => setBusy(false))
  }

  return (
    <main className="min-h-screen bg-zinc-950 px-4 py-6 text-zinc-100 sm:px-8">
      <div className="mx-auto flex max-w-6xl flex-col gap-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Trip planner</h1>
            <p className="text-zinc-400">
              {trip?.ride ? `${trip.ride.pickup} → ${trip.dropoff?.destination ?? trip.ride.destination}` : 'Ride comfort and accessible drop-off from public map data'}
            </p>
          </div>
          <div className="flex items-center gap-3 text-sm">
            {trip && <span className="text-zinc-500">Computed {new Date(trip.generated_at).toLocaleString()}</span>}
            <button
              className="rounded border border-zinc-700 px-3 py-1.5 hover:bg-zinc-800 disabled:opacity-50"
              disabled={busy}
              onClick={() => void load(true)}
              title="Ask OpenStreetMap, OSRM and USGS again (free). Falls back to cached data if they fail."
            >
              {busy ? 'Working…' : 'Recompute from open data'}
            </button>
          </div>
        </header>

        {error && <p className="rounded border border-red-900 bg-red-950/50 p-3 text-red-200">{error}</p>}
        {trip && trip.notes.length > 0 && <Notes notes={trip.notes} />}
        {trip?.ride && <RideSection trip={trip} />}
        {trip?.dropoff && <DropoffSection trip={trip} selected={selected} onSelect={setSelected} />}

        {trip && (
          <footer className="border-t border-zinc-800 pt-4 text-xs text-zinc-500">
            <p>
              Data: {trip.attribution.join(' · ')} · Routing: OSRM on{' '}
              <a className="underline" href="https://routing.openstreetmap.de/about.html">routing.openstreetmap.de</a> (FOSSGIS) ·{' '}
              <a className="underline" href="https://www.openstreetmap.org/fixthemap">Fix the map</a>
            </p>
            <p className="mt-1">Prototype for communication. Not a medical device. Not for navigation.</p>
          </footer>
        )}
      </div>
    </main>
  )
}

function Notes({ notes }: { notes: string[] }) {
  return (
    <ul className="rounded border border-amber-900/60 bg-amber-950/30 p-3 text-sm text-amber-200">
      {notes.map((n) => (
        <li key={n}>{n}</li>
      ))}
    </ul>
  )
}

// ---------------------------------------------------------------------------------------------
// Layer 1: routes
// ---------------------------------------------------------------------------------------------

function RideSection({ trip }: { trip: Trip }) {
  const ride = trip.ride!
  const main = ride.tiles[0]?.route_id
  const pins: MapPin[] = useMemo(
    () => [
      { at: ride.pickup_point, label: 'A', color: '#f4f4f5', title: `Pickup: ${ride.pickup}` },
      { at: ride.destination_point, label: 'B', color: '#4ade80', title: `Drop-off: ${ride.destination}` },
    ],
    [ride],
  )
  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-2xl font-semibold">Ride comfort</h2>
        <p className="text-sm text-amber-200">{trip.duration_note}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {ride.tiles.map((t) => (
          <div key={t.route_id} className={`rounded-lg border p-4 ${t.route_id === main ? 'border-sky-400 bg-sky-950/40' : 'border-zinc-700 bg-zinc-900'}`}>
            <p className="text-xl font-semibold">
              {t.label}
              <span className="ml-2 align-middle text-xs font-normal text-zinc-400">estimate, no traffic</span>
            </p>
            <p className="mt-1 text-zinc-300">{t.detail}</p>
            <p className="mt-2 text-xs text-zinc-500">
              {t.ride_profile ? `RideProfile: ${t.ride_profile.route_preference}` : 'RideProfile cannot name a specific alternative route'}
            </p>
          </div>
        ))}
      </div>
      <TripMap routes={ride.routes} highlightRouteId={main} pins={pins} height={380} />
      <RouteTable routes={ride.routes} />
      {ride.notes.length > 0 && <Notes notes={ride.notes} />}
    </section>
  )
}

function RouteTable({ routes }: { routes: RouteStats[] }) {
  const rows: [string, (r: RouteStats) => string][] = [
    ['Time (estimate, no traffic)', (r) => minutes(r.duration_s)],
    ['Distance', (r) => km(r.distance_m)],
    ['Via', (r) => r.summary || 'not given'],
    ['Sharp turns', (r) => String(r.sharp_turns)],
    ['Traffic lights (intersections)', (r) => String(r.traffic_signals)],
    ['Stop signs', (r) => String(r.stop_signs)],
    ['Traffic calming', (r) => calmingText(r.traffic_calming_kinds)],
    ['Climb', (r) => (r.climb_m === null ? 'unknown' : `${r.climb_m} m`)],
    ['Steepest grade', (r) => (r.steepest_grade_pct === null ? 'unknown' : `${r.steepest_grade_pct}%`)],
    ['Rough surface', (r) => `${r.rough_m} m`],
    ['Surface tagged in OSM', (r) => `${r.surface_known_pct}% of the route`],
    ['Bridges (left out of grade)', (r) => `${r.bridge_m} m`],
    ['Comfort cost (lower is smoother)', (r) => r.comfort_cost.toFixed(1)],
  ]
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-zinc-400">
            <th className="py-2 pr-4 font-normal" />
            {routes.map((r) => (
              <th key={r.id} className="py-2 pr-4 font-semibold text-zinc-100">
                {r.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, get]) => (
            <tr key={name} className="border-b border-zinc-900">
              <td className="py-1.5 pr-4 text-zinc-400">{name}</td>
              {routes.map((r) => (
                <td key={r.id} className="py-1.5 pr-4 tabular-nums">
                  {get(r)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 text-xs text-zinc-500">
        Elevation: {routes[0]?.elevation_source ?? 'none'}. Counts are what OpenStreetMap has mapped on the road the route drives; a missing sign or bump in OSM is not proof there is none.
      </p>
    </div>
  )
}

// ---------------------------------------------------------------------------------------------
// Layer 2: drop-off
// ---------------------------------------------------------------------------------------------

function DropoffSection({ trip, selected, onSelect }: { trip: Trip; selected?: string; onSelect: (id: string) => void }) {
  const d = trip.dropoff!
  const pins: MapPin[] = useMemo(() => [{ at: d.destination_point, label: '', color: '#a78bfa', title: 'Search centre' }], [d])
  return (
    <section className="flex flex-col gap-4">
      <div>
        <h2 className="text-2xl font-semibold">Accessible drop-off</h2>
        <p className="text-zinc-400">
          {d.destination}. {d.candidates.length} stopping points within {d.radius_m} m, ranked. Unknown data counts against a candidate, never for it.
        </p>
      </div>
      {d.notes.length > 0 && <Notes notes={d.notes} />}
      <div className="grid gap-4 lg:grid-cols-[1fr_1fr]">
        <TripMap candidates={d.candidates} selectedId={selected} onSelect={onSelect} pins={pins} height={460} />
        <ol className="flex max-h-[460px] flex-col gap-2 overflow-y-auto pr-1">
          {d.candidates.map((c) => (
            <CandidateRow key={c.id} c={c} open={c.id === selected} onSelect={onSelect} />
          ))}
        </ol>
      </div>
      {d.request && (
        <details className="rounded border border-zinc-800 p-3 text-sm">
          <summary className="cursor-pointer text-zinc-300">DropoffRequest sent to the car (proto clench.rider.v1)</summary>
          <pre className="mt-2 overflow-x-auto text-xs text-zinc-400">{JSON.stringify(d.request, null, 2)}</pre>
        </details>
      )}
    </section>
  )
}

function CandidateRow({ c, open, onSelect }: { c: Candidate; open: boolean; onSelect: (id: string) => void }) {
  return (
    <li className={`rounded-lg border p-3 ${open ? 'border-green-500 bg-green-950/30' : 'border-zinc-800 bg-zinc-900'}`}>
      <button className="flex w-full items-start gap-3 text-left" onClick={() => onSelect(c.id)}>
        <span className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-sm font-bold text-black ${open ? 'bg-green-400' : 'bg-zinc-300'}`}>{c.rank}</span>
        <span className="flex-1">
          <span className="block font-semibold">{c.description}</span>
          <span className="block text-sm text-zinc-300">{c.reason}</span>
          <span className="block text-xs text-zinc-500">
            {c.walk_m.toFixed(0)} m, {c.walk_kind}
            {c.slope ? ` · max grade ${c.slope.max_grade_pct}%` : ''}
          </span>
        </span>
        <span className="text-lg font-bold tabular-nums">{c.score.toFixed(0)}</span>
      </button>
      {open && (
        <div className="mt-3 border-t border-zinc-800 pt-2">
          <table className="w-full text-xs">
            <tbody>
              {c.factors.map((f) => (
                <tr key={f.name} className="align-top">
                  <td className="py-0.5 pr-2 text-zinc-400">{f.label}</td>
                  <td className={`py-0.5 pr-2 tabular-nums ${f.value === null ? 'text-amber-300' : ''}`}>{factorText(f.value)}</td>
                  <td className="py-0.5 text-zinc-400">{f.evidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <a className="mt-2 inline-block text-sm text-sky-300 underline" href={`/trip/live?candidate=${c.id}`}>
            Live Street View check (Google, separate page, not scored)
          </a>
        </div>
      )}
    </li>
  )
}
