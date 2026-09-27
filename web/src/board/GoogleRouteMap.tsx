import { useEffect, useRef } from 'react'
import type { CarState, Lang } from '../contracts'
import { loadGoogleMaps, onMapsAuthFailure, tripRoute, type LatLng, type MapsApi } from './googleMaps'
import { useRideProgress } from './rideProgress'
import { STRINGS } from './strings'

/**
 * The trip map on Google Maps: our open-data route from /api/geo/trip (OSRM, FIU to the MDC Kendall
 * drop-off; no Google Directions, decisions #26), the part already driven in grey, and the car as a blue arrow moving along the route as the ride goes
 * on. The map cannot be dragged or zoomed (a stray touch must not move it). If the script, the key or
 * the route fail, `onFail` hands over to the drawn map.
 */

const EARTH_M = 6_371_000

function metres(a: LatLng, b: LatLng): number {
  const toRad = (d: number) => (d * Math.PI) / 180
  const dLat = toRad(b.lat() - a.lat())
  const dLng = toRad(b.lng() - a.lng())
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.lat())) * Math.cos(toRad(b.lat())) * Math.sin(dLng / 2) ** 2
  return 2 * EARTH_M * Math.asin(Math.sqrt(h))
}

function bearing(a: LatLng, b: LatLng): number {
  const toRad = (d: number) => (d * Math.PI) / 180
  const y = Math.sin(toRad(b.lng() - a.lng())) * Math.cos(toRad(b.lat()))
  const x =
    Math.cos(toRad(a.lat())) * Math.sin(toRad(b.lat())) -
    Math.sin(toRad(a.lat())) * Math.cos(toRad(b.lat())) * Math.cos(toRad(b.lng() - a.lng()))
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360
}

interface Drawn {
  api: MapsApi
  path: LatLng[]
  cumulative: number[] // metres from the start to each point
  car: { setPosition(p: LatLng): void; setIcon(icon: Record<string, unknown>): void }
  driven: { setPath(path: LatLng[]): void }
}

export function GoogleRouteMap({ car, lang, apiKey, onFail }: { car: CarState | null; lang: Lang; apiKey: string; onFail: (why: string) => void }) {
  const s = STRINGS[lang].trip
  const box = useRef<HTMLDivElement | null>(null)
  const drawn = useRef<Drawn | null>(null)
  const progress = useRideProgress(car)
  const failRef = useRef(onFail)
  useEffect(() => {
    failRef.current = onFail
  }, [onFail])

  useEffect(() => {
    let live = true
    const stop = onMapsAuthFailure(() => failRef.current('Google refused the Maps key'))
    Promise.all([loadGoogleMaps(apiKey, lang), tripRoute()])
      .then(([api, route]) => {
        if (!live || !box.current) return
        const map = new api.Map(box.current, {
          center: { lat: 25.76, lng: -80.3 },
          zoom: 12,
          disableDefaultUI: true,
          gestureHandling: 'none',
          keyboardShortcuts: false,
          clickableIcons: false,
        })
        const path = route.path.map(([la, ln]) => new api.LatLng(la, ln))
        if (path.length < 2) {
          failRef.current('the route has no points')
          return
        }
        new api.Polyline({ map, path, strokeColor: '#2563eb', strokeWeight: 7, strokeOpacity: 0.9 })
        const bounds = new api.LatLngBounds()
        path.forEach((p) => bounds.extend(p))
        map.fitBounds(bounds, 24)
        if (route.dropoff) new api.Marker({ map, position: new api.LatLng(route.dropoff[0], route.dropoff[1]), zIndex: 8 })
        const cumulative = [0]
        for (let i = 1; i < path.length; i++) cumulative.push(cumulative[i - 1] + metres(path[i - 1], path[i]))
        const driven = new api.Polyline({ map, path: [path[0]], strokeColor: '#9ca3af', strokeWeight: 7, zIndex: 5 })
        const marker = new api.Marker({ map, position: path[0], zIndex: 10, icon: arrow(api, 0) })
        drawn.current = { api, path, cumulative, car: marker, driven }
      })
      .catch((e: unknown) => live && failRef.current(String(e)))
    return () => {
      live = false
      stop()
    }
  }, [apiKey, lang])

  // Move the car along the route.
  useEffect(() => {
    const d = drawn.current
    if (!d || d.path.length < 2) return
    const total = d.cumulative[d.cumulative.length - 1]
    const at = progress * total
    let i = d.cumulative.findIndex((c) => c >= at)
    if (i <= 0) i = 1
    const [a, b] = [d.path[i - 1], d.path[i]]
    const span = d.cumulative[i] - d.cumulative[i - 1] || 1
    const t = Math.min(1, Math.max(0, (at - d.cumulative[i - 1]) / span))
    const here = new d.api.LatLng(a.lat() + (b.lat() - a.lat()) * t, a.lng() + (b.lng() - a.lng()) * t)
    d.car.setPosition(here)
    d.car.setIcon(arrow(d.api, bearing(a, b)))
    d.driven.setPath([...d.path.slice(0, i), here])
  }, [progress])

  return (
    <div className="relative h-full w-full overflow-hidden rounded-3xl shadow-xl shadow-black/15 ring-1 ring-black/10">
      <div ref={box} className="h-full w-full" aria-label={s.mapLabel} />
      <div className="absolute left-4 top-4 rounded-full bg-white/90 px-4 py-1.5 text-xl font-semibold text-zinc-800 shadow">
        {car ? `${car.eta_min} ${s.min}` : '–'} · {s.destination}
      </div>
    </div>
  )
}

function arrow(api: MapsApi, rotation: number) {
  return {
    path: api.SymbolPath.FORWARD_CLOSED_ARROW,
    scale: 7,
    rotation,
    fillColor: '#2563eb',
    fillOpacity: 1,
    strokeColor: '#ffffff',
    strokeWeight: 2,
  }
}
