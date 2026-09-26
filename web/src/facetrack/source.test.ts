import { describe, expect, it } from 'vitest'
import { GAZE_MIN_CONFIDENCE, GAZE_STALE_MS, GazeFeed } from './gaze'
import { DEFAULT_RANGE } from './pose'
import { chooseSource, fromHead, POINT_SOURCE, type PointSample } from './source'

describe('chooseSource', () => {
  it('webcam follows the head, gaze the gaze, scan and head tilt nothing', () => {
    expect(chooseSource('webcam', true)).toBe('head')
    expect(chooseSource('gaze', false)).toBe('gaze')
    expect(chooseSource('scan', true)).toBeNull()
    expect(chooseSource('headtilt', true)).toBeNull()
    expect(chooseSource(null, true)).toBeNull()
  })

  it('auto uses the gaze if available, else the head', () => {
    expect(chooseSource('auto', true)).toBe('gaze')
    expect(chooseSource('auto', false)).toBe('head')
  })

  it('maps sources to POINT sources', () => {
    expect(POINT_SOURCE).toEqual({ head: 'webcam', gaze: 'gaze' })
  })
})

describe('fromHead', () => {
  it('turns a head frame into a point sample', () => {
    const s = fromHead({ t: 5, face: true, raw: null, angles: { yaw: 0, pitch: -8 } }, DEFAULT_RANGE)
    expect(s).toMatchObject({ source: 'head', t: 5, found: true, confidence: 1 })
    expect(s.point?.x).toBeCloseTo(0.5)
    expect(s.point?.y).toBeCloseTo(0.5)
  })

  it('no face: not found, no point', () => {
    expect(fromHead({ t: 5, face: false, raw: null, angles: null }, DEFAULT_RANGE)).toMatchObject({
      found: false,
      point: null,
      confidence: 0,
    })
  })
})

describe('GazeFeed', () => {
  function feedAt() {
    let now = 1000
    const feed = new GazeFeed(() => now)
    const samples: PointSample[] = []
    feed.subscribe((s) => samples.push(s))
    return { feed, samples, at: (t: number) => (now = t) }
  }

  it('publishes gaze samples with clamped points', () => {
    const { feed, samples } = feedAt()
    feed.feed({ x: 1.4, y: -0.2, found: true, confidence: 0.9 })
    expect(samples).toEqual([{ source: 'gaze', t: 1000, found: true, point: { x: 1, y: 0 }, confidence: 0.9 }])
  })

  it('is available only while fresh, found and confident', () => {
    const { feed, at } = feedAt()
    expect(feed.available()).toBe(false) // nothing fed yet
    feed.feed({ x: 0.5, y: 0.5, found: true, confidence: 0.8 })
    expect(feed.available()).toBe(true)
    at(1000 + GAZE_STALE_MS + 1)
    expect(feed.available()).toBe(false) // the tracker stopped feeding
    expect(feed.connected()).toBe(false)
    feed.feed({ x: 0.5, y: 0.5, found: false, confidence: 0.9 })
    expect(feed.available()).toBe(false) // eyes not found
    expect(feed.connected()).toBe(true)
    feed.feed({ x: 0.5, y: 0.5, found: true, confidence: GAZE_MIN_CONFIDENCE - 0.01 })
    expect(feed.available()).toBe(false) // not sure enough
    feed.feed({ x: 0.5, y: 0.5, found: true, confidence: GAZE_MIN_CONFIDENCE })
    expect(feed.available()).toBe(true)
    feed.clear()
    expect(feed.available()).toBe(false)
  })

  it('a low-confidence point is reported but not found', () => {
    const { feed, samples } = feedAt()
    feed.feed({ x: 0.3, y: 0.3, found: true, confidence: 0.2 })
    expect(samples[0]).toMatchObject({ found: false, point: { x: 0.3, y: 0.3 } })
  })
})
