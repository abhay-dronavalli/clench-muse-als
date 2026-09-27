import { useEffect, useState } from 'react'
import { PLACE_FIELDS, liveWarning, placeField } from './format'
import type { Candidate, LiveCandidate, LivePlace, Trip } from './types'

// Google's attribution text style (Street View / Places policies): Roboto 12-16 sp, weight 400.
const GOOGLE_ATTRIBUTION = { fontFamily: 'Roboto, Arial, sans-serif', fontSize: 13, fontWeight: 400 }

/** /trip/live: live Google evidence for one drop-off candidate. Nothing here is stored, scored,
 * spoken, or sent to the car, and there is no map on this page (docs/decisions.md #22). */
export default function LivePage() {
  const id = new URLSearchParams(window.location.search).get('candidate') ?? ''
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [live, setLive] = useState<LiveCandidate | null>(null)
  const [place, setPlace] = useState<LivePlace | null>(null)
  const [busy, setBusy] = useState<'candidate' | 'place' | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/geo/trip')
      .then((r) => r.json())
      .then((t: Trip) => setCandidates(t.dropoff?.candidates ?? []))
      .catch(() => setCandidates([]))
  }, [])

  const current = candidates.find((c) => c.id === id)

  async function run<T>(kind: 'candidate' | 'place', url: string, set: (v: T) => void) {
    setBusy(kind)
    setError(null)
    try {
      const res = await fetch(url)
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`)
      set(body as T)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(null)
    }
  }

  const warning = liveWarning(live?.vision?.label)

  return (
    <main className="min-h-screen bg-zinc-950 px-4 py-6 text-zinc-100 sm:px-8">
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <header>
          <a className="text-sm text-sky-300 underline" href="/trip">
            ← Trip planner
          </a>
          <h1 className="mt-2 text-3xl font-bold">Live check</h1>
          <p className="mt-1 text-zinc-400">
            Fetched from Google at the moment you ask. It is shown here only: never stored, never used to rank candidates, never spoken, never sent to the car.
          </p>
        </header>

        {candidates.length > 0 && (
          <nav className="flex flex-wrap gap-2 text-sm">
            {candidates.map((c) => (
              <a key={c.id} href={`/trip/live?candidate=${c.id}`} className={`rounded border px-2 py-1 ${c.id === id ? 'border-green-500 text-green-300' : 'border-zinc-700 text-zinc-300'}`}>
                #{c.rank}
              </a>
            ))}
          </nav>
        )}

        {current ? (
          <section className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
            <p className="text-sm text-zinc-400">Candidate #{current.rank} (open-data score {current.score.toFixed(0)})</p>
            <p className="text-lg font-semibold">{current.description}</p>
            <p className="text-zinc-300">{current.reason}</p>
            <button
              className="mt-3 rounded bg-zinc-100 px-3 py-1.5 font-semibold text-zinc-900 disabled:opacity-50"
              disabled={busy !== null}
              onClick={() => void run<LiveCandidate>('candidate', `/api/geo/live/candidate/${id}`, setLive)}
            >
              {busy === 'candidate' ? 'Checking…' : 'Check Street View now'}
            </button>
            <span className="ml-3 text-xs text-zinc-500">One Street View image and one Gemini request.</span>
          </section>
        ) : (
          <p className="text-zinc-400">{id ? `No candidate ${id} in the current trip.` : 'Pick a candidate above.'}</p>
        )}

        {error && <p className="rounded border border-red-900 bg-red-950/50 p-3 text-red-200">{error}</p>}

        {live && (
          <section className="flex flex-col gap-3">
            {warning && (
              <div className="rounded border border-amber-700 bg-amber-950/50 p-3 text-amber-200">
                <p className="font-semibold">Warning: {warning}</p>
                <p className="text-sm">The ranking is not changed. Google's terms do not allow Street View content to change our results or what is sent to the car; check the entrance another way.</p>
              </div>
            )}
            {live.streetview?.image ? (
              <figure>
                <img src={live.streetview.image} alt="Street View facing the entrance" className="w-full rounded-lg" />
                <figcaption className="mt-1 flex justify-between text-xs text-zinc-400">
                  <span>
                    Imagery {live.streetview.date ?? 'date unknown'}, facing {live.streetview.heading_deg}° from {live.streetview.pano_to_stop_m} m off the stopping point
                  </span>
                  <span style={GOOGLE_ATTRIBUTION}>{live.streetview.copyright ?? '© Google'} · Google Maps</span>
                </figcaption>
              </figure>
            ) : (
              <p className="text-zinc-400">No Street View here{live.streetview ? ` (${live.streetview.status})` : ''}.</p>
            )}
            {live.vision && (
              <div className="rounded border border-zinc-800 p-3">
                <p>
                  Gemini reads the image as <span className="font-semibold">{live.vision.label.replaceAll('_', ' ')}</span> (confidence {live.vision.confidence.toFixed(2)})
                </p>
                <p className="text-sm text-zinc-300">{live.vision.note}</p>
                <p className="text-xs text-zinc-500">Cues seen: {live.vision.cues.length ? live.vision.cues.join(', ').replaceAll('_', ' ') : 'none'}</p>
              </div>
            )}
            {live.errors.length > 0 && <p className="text-sm text-amber-300">{live.errors.join('; ')}</p>}
          </section>
        )}

        <section className="rounded-lg border border-zinc-800 p-4">
          <div className="flex items-center justify-between">
            <h2 className="font-semibold">Destination accessibility (Google Places)</h2>
            <button className="rounded border border-zinc-700 px-3 py-1 text-sm disabled:opacity-50" disabled={busy !== null} onClick={() => void run<LivePlace>('place', '/api/geo/live/place', setPlace)}>
              {busy === 'place' ? 'Checking…' : 'Check now'}
            </button>
          </div>
          {place?.error && <p className="mt-2 text-sm text-amber-300">{place.error}</p>}
          {place?.accessibility && (
            <>
              <p className="mt-2 text-sm text-zinc-400">{place.name}: as Google reports it. A field Google leaves out is unknown.</p>
              <dl className="mt-2 grid grid-cols-2 gap-1 text-sm sm:grid-cols-4">
                {PLACE_FIELDS.map(([key, label]) => {
                  const v = placeField(place.accessibility, key)
                  return (
                    <div key={key} className="rounded bg-zinc-900 p-2">
                      <dt className="text-zinc-400">{label}</dt>
                      <dd className={v === 'unknown' ? 'text-amber-300' : ''}>{v}</dd>
                    </div>
                  )
                })}
              </dl>
              <p className="mt-2 text-right" style={GOOGLE_ATTRIBUTION}>
                Google Maps
              </p>
            </>
          )}
        </section>
      </div>
    </main>
  )
}
