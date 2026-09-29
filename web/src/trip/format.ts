// Pure helpers for the trip pages (tested in format.test.ts).

/** Google's encoded polyline format (core/geo/geometry.encode_polyline), precision 5. */
export function decodePolyline(encoded: string, precision = 5): [number, number][] {
  const factor = 10 ** precision
  const points: [number, number][] = []
  let index = 0
  let lat = 0
  let lon = 0
  while (index < encoded.length) {
    const deltas: number[] = []
    for (let k = 0; k < 2; k++) {
      let shift = 0
      let result = 0
      let b: number
      do {
        b = encoded.charCodeAt(index++) - 63
        result |= (b & 0x1f) << shift
        shift += 5
      } while (b >= 0x20)
      deltas.push(result & 1 ? ~(result >> 1) : result >> 1)
    }
    lat += deltas[0]
    lon += deltas[1]
    points.push([lat / factor, lon / factor])
  }
  return points
}

export function minutes(seconds: number): string {
  return `${Math.max(1, Math.round(seconds / 60))} min`
}

export function km(meters: number): string {
  return `${(meters / 1000).toFixed(1)} km`
}

/** A factor value for display: unknown stays unknown, never a number. */
export function factorText(value: number | null): string {
  return value === null ? 'unknown' : value.toFixed(2)
}

/** A Places accessibility field: true / false as Google returned it, absent = unknown. */
export function placeField(fields: Record<string, boolean> | undefined, key: string): 'yes' | 'no' | 'unknown' {
  if (!fields || !(key in fields)) return 'unknown'
  return fields[key] ? 'yes' : 'no'
}

export const PLACE_FIELDS: [string, string][] = [
  ['wheelchairAccessibleEntrance', 'Entrance'],
  ['wheelchairAccessibleParking', 'Parking'],
  ['wheelchairAccessibleRestroom', 'Restroom'],
  ['wheelchairAccessibleSeating', 'Seating'],
]

/** Speed bumps among traffic_calming kinds (the same set as core/geo/comfort.SPEED_BUMPS). */
const SPEED_BUMPS = new Set(['bump', 'hump', 'table', 'cushion', 'mini_bumps', 'yes'])

export function calmingText(kinds: Record<string, number>): string {
  const entries = Object.entries(kinds)
  if (entries.length === 0) return 'none mapped'
  return entries
    .sort((a, b) => b[1] - a[1])
    .map(([kind, n]) => `${n} ${kind.replace('_', ' ')}${SPEED_BUMPS.has(kind) ? '' : ' (not a bump)'}`)
    .join(', ')
}

/** The live warning. The ranking is never changed by Google content (docs/decisions.md #25). */
export function liveWarning(label: string | undefined): string | null {
  if (label === 'steps') return 'The live image shows steps at this entrance, and no sign of a step-free way.'
  if (label === 'steps_with_accessible_route') return 'The live image shows steps, with a sign of an accessible route nearby.'
  return null
}
