import { useEffect, useRef, useState } from 'react'
import type { CarState, Lang } from '../contracts'
import { STRINGS } from './strings'

/**
 * The trip's route map: where the car is on its way to the destination.
 *
 * For now a drawn, offline map (no key, no network): a city of streets, parks and a river, the route,
 * the part already driven, the car and the destination. Like a navigation app it follows the car: the
 * view stays centred on it and takes the shape of its box (half the upper area in Split, all of it in
 * Map). The car moves along the route as the ride goes on (CAR_STATE: arrival counting down, smoothed
 * between updates while the car moves).
 *
 * Google Maps later: keep this component's props (`car`, `lang`) and swap the SVG for a Google map
 * (e.g. @vis.gl/react-google-maps with a DirectionsRenderer) when VITE_GOOGLE_MAPS_API_KEY is set; the
 * route and the car's position would then come from the ride's real origin, destination and location.
 */

// The drawn city, in SVG units.
const CITY_W = 3000
const CITY_H = 1200
const STEP_X = 120
const STEP_Y = 110
const VIEW_H = 760 // how much of the city's height shows; the width follows the box's shape

const STREETS_X = Array.from({ length: CITY_W / STEP_X + 1 }, (_, i) => i * STEP_X)
const STREETS_Y = Array.from({ length: Math.floor(CITY_H / STEP_Y) + 1 }, (_, i) => i * STEP_Y)
const major = (i: number) => i % 4 === 2

// A few blocks are parks: a fixed, even scatter (the same every time).
const PARKS = Array.from({ length: 26 }, (_, k) => {
  const i = (k * 7 + 3) % (STREETS_X.length - 1)
  const j = (k * 5 + 2) % (STREETS_Y.length - 1)
  return { x: STREETS_X[i] + 10, y: STREETS_Y[j] + 10, w: STEP_X - 20 + (k % 3 === 0 ? STEP_X : 0), h: STEP_Y - 20 }
})

// The route, along streets, from the pick-up (lower left) to the destination (upper right).
// It stays within about 460 units of height, so the whole trip fits even the wide Map layout.
const ROUTE: [number, number][] = [
  [240, 880], [240, 770], [600, 770], [600, 660], [1080, 660], [1080, 770], [1560, 770],
  [1560, 550], [2040, 550], [2040, 440], [2760, 440],
]

const MARGIN = 30 // room around the route when the whole route is framed
const ROUTE_X: [number, number] = [Math.min(...ROUTE.map((q) => q[0])), Math.max(...ROUTE.map((q) => q[0]))]
const ROUTE_Y: [number, number] = [Math.min(...ROUTE.map((q) => q[1])) - 45, Math.max(...ROUTE.map((q) => q[1]))] // - the pin

function lengths(points: [number, number][]) {
  const segs = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]))
  return { segs, total: segs.reduce((a, b) => a + b, 0) }
}
const { segs: SEGS, total: TOTAL } = lengths(ROUTE)

/** The point `share` (0..1) of the way along the route, the heading there, and the driven part. */
function along(share: number) {
  let left = Math.max(0, Math.min(1, share)) * TOTAL
  const driven: [number, number][] = [ROUTE[0]]
  for (let i = 0; i < SEGS.length; i++) {
    const [a, b] = [ROUTE[i], ROUTE[i + 1]]
    if (left <= SEGS[i] || i === SEGS.length - 1) {
      const t = SEGS[i] === 0 ? 0 : Math.min(1, left / SEGS[i])
      const p: [number, number] = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
      driven.push(p)
      return { p, heading: (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI, driven }
    }
    left -= SEGS[i]
    driven.push(b)
  }
  return { p: ROUTE[ROUTE.length - 1], heading: 0, driven }
}

const pts = (list: [number, number][]) => list.map(([x, y]) => `${x},${y}`).join(' ')

/** How far along the ride is (0..1): arrival counted down from the first CAR_STATE of the trip. */
function useProgress(car: CarState | null): number {
  const [progress, setProgress] = useState(0)
  // start: arrival minutes when the trip began; lastAt: when the last CAR_STATE arrived (ms).
  const ride = useRef<{ start: number | null; lastAt: number; car: CarState | null }>({ start: null, lastAt: 0, car: null })
  useEffect(() => {
    if (!car) return
    const r = ride.current
    r.car = car
    r.lastAt = Date.now()
    if (r.start === null || car.eta_min > r.start) r.start = Math.max(car.eta_min, 1)
  }, [car])
  useEffect(() => {
    // Twice a second: the arrival count, plus how far into the current minute the moving car is.
    const timer = window.setInterval(() => {
      const { start, lastAt, car: c } = ride.current
      if (!c || start === null) return
      const minuteShare = c.speed_mph > 0 ? Math.min(1, (Date.now() - lastAt) / 60_000) : 0
      setProgress(Math.min(1, (start - c.eta_min + minuteShare) / start))
    }, 500)
    return () => window.clearInterval(timer)
  }, [])
  return progress
}

/** The box's width / height, so the view takes its shape (Split is about 2.7:1, Map about 5:1). */
function useAspect(el: React.RefObject<HTMLDivElement | null>): number {
  const [aspect, setAspect] = useState(2.5)
  useEffect(() => {
    const node = el.current
    if (!node) return
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setAspect(width / height)
    })
    observer.observe(node)
    return () => observer.disconnect()
  }, [el])
  return aspect
}

export function RouteMap({ car, lang }: { car: CarState | null; lang: Lang }) {
  const s = STRINGS[lang].trip
  const box = useRef<HTMLDivElement | null>(null)
  const aspect = useAspect(box)
  const progress = useProgress(car)
  const { p, heading, driven } = along(progress)
  const end = ROUTE[ROUTE.length - 1]
  // On each axis: if the whole route fits, frame all of it; otherwise follow the car, leading a little
  // toward the destination (more road ahead than behind). Always kept inside the city.
  const w = Math.min(CITY_W, VIEW_H * aspect)
  const h = Math.min(CITY_H, w / aspect)
  const frame = (size: number, [lo, hi]: [number, number], car: number, lead: number, limit: number) => {
    const start = size >= hi - lo + 2 * MARGIN ? (lo + hi) / 2 - size / 2 : car - size * lead
    return Math.max(0, Math.min(limit - size, start))
  }
  const x = frame(w, ROUTE_X, p[0], 0.35, CITY_W)
  const y = frame(h, ROUTE_Y, p[1], 0.6, CITY_H)
  return (
    <div ref={box} className="relative h-full w-full overflow-hidden rounded-3xl bg-[#eef1ea] shadow-xl shadow-black/15 ring-1 ring-black/10">
      <svg viewBox={`${x} ${y} ${w} ${h}`} preserveAspectRatio="xMidYMid slice" className="h-full w-full" aria-label={s.mapLabel}>
        <rect x={0} y={0} width={CITY_W} height={CITY_H} fill="#eef1ea" />
        {PARKS.map((k, i) => (
          <rect key={i} x={k.x} y={k.y} width={k.w} height={k.h} rx={14} fill="#cfe6c4" />
        ))}
        <path
          d={`M0 ${CITY_H * 0.62} C 500 ${CITY_H * 0.56}, 800 ${CITY_H * 0.7}, 1400 ${CITY_H * 0.63} S 2400 ${CITY_H * 0.55}, ${CITY_W} ${CITY_H * 0.6}`}
          fill="none"
          stroke="#b9d8f0"
          strokeWidth={46}
        />
        {STREETS_X.map((sx, i) => (
          <line key={`x${sx}`} x1={sx} y1={0} x2={sx} y2={CITY_H} stroke="#ffffff" strokeWidth={major(i) ? 20 : 11} />
        ))}
        {STREETS_Y.map((sy, i) => (
          <line key={`y${sy}`} x1={0} y1={sy} x2={CITY_W} y2={sy} stroke="#ffffff" strokeWidth={major(i) ? 20 : 11} />
        ))}
        {/* The route: ahead in blue, driven in grey. */}
        <polyline points={pts(ROUTE)} fill="none" stroke="#1d4ed8" strokeWidth={18} strokeLinejoin="round" strokeLinecap="round" opacity={0.25} />
        <polyline points={pts(ROUTE)} fill="none" stroke="#2563eb" strokeWidth={10} strokeLinejoin="round" strokeLinecap="round" />
        <polyline points={pts(driven)} fill="none" stroke="#9ca3af" strokeWidth={10} strokeLinejoin="round" strokeLinecap="round" />
        {/* Pick-up and destination. */}
        <circle cx={ROUTE[0][0]} cy={ROUTE[0][1]} r={10} fill="#ffffff" stroke="#6b7280" strokeWidth={4} />
        <g transform={`translate(${end[0]} ${end[1]})`}>
          <path d="M0 0 C -14 -22, -18 -30, -18 -40 A 18 18 0 1 1 18 -40 C 18 -30, 14 -22, 0 0 Z" fill="#dc2626" />
          <circle cx={0} cy={-40} r={7} fill="#ffffff" />
        </g>
        {/* The car: a disc with a heading arrow. */}
        <g transform={`translate(${p[0]} ${p[1]}) rotate(${heading})`}>
          <circle r={24} fill="#2563eb" opacity={0.18} />
          <circle r={15} fill="#ffffff" stroke="#2563eb" strokeWidth={4} />
          <path d="M-6 -7 L 8 0 L -6 7 Z" fill="#2563eb" />
        </g>
      </svg>
      <div className="absolute left-4 top-4 rounded-full bg-white/90 px-4 py-1.5 text-xl font-semibold text-zinc-800 shadow">
        {car ? `${car.eta_min} ${s.min}` : '–'} · {s.destination}
      </div>
      <div className="absolute bottom-3 right-4 rounded-full bg-white/80 px-3 py-0.5 text-sm text-zinc-500">{s.mapPreview}</div>
    </div>
  )
}
