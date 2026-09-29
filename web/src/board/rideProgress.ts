import { useEffect, useRef, useState } from 'react'
import type { CarState } from '../contracts'

/**
 * How far along the ride is (0..1), for the route maps: the arrival time counted down from the first
 * CAR_STATE of the trip, plus how far into the current minute a moving car is (so the car glides along
 * the route between the Core's once-a-minute updates instead of jumping).
 */
export function useRideProgress(car: CarState | null): number {
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
