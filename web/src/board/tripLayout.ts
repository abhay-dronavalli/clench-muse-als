/**
 * How the trip screen's upper part is used, remembered in this browser (the tablet keeps it too):
 *   car    the 3D car and its world (the default)
 *   split  the route map on the left, the car on the right
 *   map    the route map across the whole upper part
 * The tiles below never move between layouts, so pointing (head, gaze) aims at the same places.
 */

export type TripLayout = 'car' | 'split' | 'map'
export const TRIP_LAYOUTS: TripLayout[] = ['car', 'split', 'map']

const KEY = 'clench.tripLayout'
const listeners = new Set<() => void>()

function read(): TripLayout {
  try {
    const v = window.localStorage.getItem(KEY)
    return v === 'split' || v === 'map' ? v : 'car'
  } catch {
    return 'car'
  }
}

let current: TripLayout = typeof window === 'undefined' ? 'car' : read()

export const tripLayout = {
  get: (): TripLayout => current,
  set(next: TripLayout) {
    current = next
    try {
      window.localStorage.setItem(KEY, next)
    } catch {
      // private mode: the choice lasts until the page reloads
    }
    listeners.forEach((fn) => fn())
  },
  subscribe(fn: () => void) {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  },
}
