/**
 * Loading Google's Maps JavaScript API once, only when a key is set (VITE_GOOGLE_MAPS_API_KEY in the
 * repository's .env). The key goes to the browser, as with any Maps JavaScript key: restrict it in
 * Google Cloud to the Maps JavaScript API, and to http://localhost:5173/* and 5174. No Directions: the
 * route drawn is our own open-data route (OSRM, /api/geo/trip), docs/decisions.md #26.
 *
 * Only the handful of Maps classes the trip map uses are typed here (no @types/google.maps).
 */

export interface LatLng {
  lat(): number
  lng(): number
}

export interface MapsApi {
  Map: new (el: HTMLElement, opts: Record<string, unknown>) => { fitBounds(b: unknown, padding?: number): void }
  LatLngBounds: new () => { extend(p: LatLng): void }
  Polyline: new (opts: Record<string, unknown>) => { setPath(path: LatLng[]): void }
  Marker: new (opts: Record<string, unknown>) => { setPosition(p: LatLng): void; setIcon(icon: Record<string, unknown>): void }
  LatLng: new (lat: number, lng: number) => LatLng
  SymbolPath: { FORWARD_CLOSED_ARROW: unknown }
}

declare global {
  interface Window {
    google?: { maps: MapsApi }
    gm_authFailure?: () => void
    __clenchMapsReady?: () => void
  }
}

export const MAPS_KEY: string = import.meta.env.VITE_GOOGLE_MAPS_API_KEY ?? ''

/** The trip's route (the first route tile: fastest and smoothest) and drop-off point, from /api/geo/trip. */
export async function tripRoute(): Promise<{ path: [number, number][]; dropoff: [number, number] | null }> {
  const res = await fetch('/api/geo/trip')
  if (!res.ok) throw new Error(`no trip route (HTTP ${res.status})`)
  const trip = (await res.json()) as {
    ride: { routes: { id: string; polyline: string }[]; tiles: { route_id: string }[] } | null
    dropoff: { request: { point: { latitude: number; longitude: number } } | null } | null
  }
  const id = trip.ride?.tiles[0]?.route_id
  const route = trip.ride?.routes.find((r) => r.id === id)
  if (!route) throw new Error('the trip has no route')
  const p = trip.dropoff?.request?.point
  return { path: decodePolyline(route.polyline), dropoff: p ? [p.latitude, p.longitude] : null }
}

import { decodePolyline } from '../trip/format'

let loading: Promise<MapsApi> | null = null
const authListeners = new Set<() => void>()

/** Load the Maps JavaScript API (once). Rejects if the script cannot load. */
export function loadGoogleMaps(key: string, lang: string): Promise<MapsApi> {
  if (window.google?.maps) return Promise.resolve(window.google.maps)
  loading ??= new Promise<MapsApi>((resolve, reject) => {
    window.__clenchMapsReady = () => (window.google ? resolve(window.google.maps) : reject(new Error('no google.maps')))
    // Google calls this when the key is refused (bad key, API not enabled, referrer not allowed).
    window.gm_authFailure = () => authListeners.forEach((fn) => fn())
    const script = document.createElement('script')
    script.src =
      `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(key)}` +
      `&language=${encodeURIComponent(lang)}&v=weekly&loading=async&callback=__clenchMapsReady`
    script.async = true
    script.onerror = () => {
      loading = null
      reject(new Error('the Maps script could not load (offline?)'))
    }
    document.head.appendChild(script)
  })
  return loading
}

/** Be told when Google refuses the key (the map then falls back to the drawn one). */
export function onMapsAuthFailure(fn: () => void): () => void {
  authListeners.add(fn)
  return () => {
    authListeners.delete(fn)
  }
}
