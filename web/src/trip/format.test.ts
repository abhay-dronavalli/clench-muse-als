import { describe, expect, it } from 'vitest'
import { calmingText, decodePolyline, factorText, liveWarning, minutes, placeField } from './format'

describe('decodePolyline', () => {
  it("matches Google's published example", () => {
    const pts = decodePolyline('_p~iF~ps|U_ulLnnqC_mqNvxq`@')
    expect(pts.map(([a, b]) => [+a.toFixed(5), +b.toFixed(5)])).toEqual([
      [38.5, -120.2],
      [40.7, -120.95],
      [43.252, -126.453],
    ])
  })
})

describe('display helpers', () => {
  it('rounds minutes like the tiles do', () => {
    expect(minutes(978)).toBe('16 min')
    expect(minutes(10)).toBe('1 min')
  })

  it('shows an unknown factor as unknown, never as a number', () => {
    expect(factorText(null)).toBe('unknown')
    expect(factorText(0)).toBe('0.00')
  })

  it('reads an absent Places field as unknown and keeps explicit false', () => {
    expect(placeField({ wheelchairAccessibleEntrance: false }, 'wheelchairAccessibleEntrance')).toBe('no')
    expect(placeField({ wheelchairAccessibleEntrance: true }, 'wheelchairAccessibleEntrance')).toBe('yes')
    expect(placeField({}, 'wheelchairAccessibleEntrance')).toBe('unknown')
    expect(placeField(undefined, 'wheelchairAccessibleParking')).toBe('unknown')
  })

  it('says "none mapped" rather than "none" for missing calming', () => {
    expect(calmingText({})).toBe('none mapped')
    expect(calmingText({ table: 4, hump: 3 })).toBe('4 table, 3 hump')
    expect(calmingText({ chicane: 1 })).toBe('1 chicane (not a bump)')
  })

  it('warns only on steps labels', () => {
    expect(liveWarning('steps')).toContain('steps')
    expect(liveWarning('steps_with_accessible_route')).toContain('accessible route')
    expect(liveWarning('ramp')).toBeNull()
    expect(liveWarning(undefined)).toBeNull()
  })
})
