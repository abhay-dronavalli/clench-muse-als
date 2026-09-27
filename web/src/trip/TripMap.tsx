import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { useEffect, useRef, useState } from 'react'
import { decodePolyline } from './format'
import type { Candidate, LatLng, RouteStats } from './types'

// OpenStreetMap tiles (no key in the browser). Only open data is drawn on this map: Google
// content never appears on or next to it (docs/decisions.md #22).
const TILES = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'

const ll = (p: LatLng): [number, number] => [p.latitude, p.longitude]

export interface MapPin {
  at: LatLng
  label: string
  color: string
  title: string
}

interface Props {
  routes?: RouteStats[]
  highlightRouteId?: string
  candidates?: Candidate[]
  selectedId?: string
  onSelect?: (id: string) => void
  pins?: MapPin[]
  height?: number
}

/** A Leaflet map. When tiles fail to load (offline) the background stays plain and a note says so. */
export function TripMap({ routes = [], highlightRouteId, candidates = [], selectedId, onSelect, pins = [], height = 360 }: Props) {
  const el = useRef<HTMLDivElement>(null)
  const [tilesOffline, setTilesOffline] = useState(false)

  useEffect(() => {
    if (!el.current) return
    const map = L.map(el.current, { zoomControl: true, attributionControl: true })
    let loaded = 0
    let failed = 0
    L.tileLayer(TILES, { maxZoom: 19, attribution: ATTRIBUTION })
      .on('tileload', () => {
        loaded++
        setTilesOffline(false)
      })
      .on('tileerror', () => {
        failed++
        if (loaded === 0 && failed >= 2) setTilesOffline(true)
      })
      .addTo(map)
    const bounds = L.latLngBounds([])

    // Routes: the highlighted one on top.
    const ordered = [...routes].sort((a, b) => Number(a.id === highlightRouteId) - Number(b.id === highlightRouteId))
    for (const r of ordered) {
      const line = decodePolyline(r.polyline)
      const main = r.id === highlightRouteId
      // A dark casing under each line keeps it readable over the map's own colored roads.
      L.polyline(line, { color: '#18181b', weight: main ? 9 : 7, opacity: 0.7 }).addTo(map)
      L.polyline(line, { color: main ? '#38bdf8' : '#f59e0b', weight: main ? 5 : 4, opacity: 1, dashArray: main ? undefined : '10 8' })
        .bindTooltip(`${r.label}: ${r.summary}`)
        .addTo(map)
      line.forEach((p) => bounds.extend(p))
    }

    // Drop-off candidates: the walk (dashed), the stop (numbered), the entrance (small dot).
    for (const c of candidates) {
      const selected = c.id === selectedId
      const walk = decodePolyline(c.walk_polyline)
      L.polyline(walk, { color: selected ? '#4ade80' : '#86efac', weight: selected ? 4 : 2, dashArray: '4 6', opacity: selected ? 1 : 0.6 }).addTo(map)
      L.circleMarker(ll(c.entrance), { radius: 4, color: '#fde047', fillOpacity: 1 }).bindTooltip('Entrance').addTo(map)
      const icon = L.divIcon({
        className: '',
        html: `<div style="width:26px;height:26px;border-radius:13px;display:flex;align-items:center;justify-content:center;font:700 13px system-ui;color:#000;background:${selected ? '#4ade80' : '#e4e4e7'};border:2px solid #000">${c.rank}</div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
      })
      L.marker(ll(c.stop), { icon, title: `#${c.rank} ${c.description}` })
        .on('click', () => onSelect?.(c.id))
        .addTo(map)
      bounds.extend(ll(c.stop))
      bounds.extend(ll(c.entrance))
    }

    for (const p of pins) {
      L.circleMarker(ll(p.at), { radius: 7, color: '#000', weight: 2, fillColor: p.color, fillOpacity: 1 })
        .bindTooltip(p.title)
        .addTo(map)
      bounds.extend(ll(p.at))
    }

    if (bounds.isValid()) map.fitBounds(bounds, { padding: [24, 24] })
    else map.setView([25.76, -80.37], 12)
    return () => {
      map.remove()
    }
  }, [routes, highlightRouteId, candidates, selectedId, onSelect, pins])

  return (
    <div className="relative overflow-hidden rounded-lg border border-zinc-800" style={{ height }}>
      <div ref={el} className="h-full w-full bg-zinc-900" />
      {tilesOffline && (
        <div className="pointer-events-none absolute left-2 top-2 z-[1000] rounded bg-zinc-950/90 px-2 py-1 text-xs text-amber-300">
          Map tiles offline: routes and points are drawn on a blank background.
        </div>
      )}
    </div>
  )
}
