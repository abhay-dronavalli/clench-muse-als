import { describe, expect, it } from 'vitest'
import { toGazeInput, whyNotRunning } from './eyedidWeb'

const ok = { key: 'k', nativeShell: false, isolated: true }

describe('toGazeInput', () => {
  it('turns page pixels into fractions of the window', () => {
    expect(toGazeInput({ x: 480, y: 270, trackingState: 0 }, 1920, 1080)).toEqual({
      x: 0.25, y: 0.25, found: true, confidence: 1, state: 'SUCCESS',
    })
  })

  it('keeps a low-confidence point at the slot threshold', () => {
    const g = toGazeInput({ x: 960, y: 540, trackingState: 1 }, 1920, 1080)
    expect(g.found).toBe(true)
    expect(g.confidence).toBe(0.5)
    expect(g.state).toBe('LOW_CONFIDENCE')
  })

  it('reports a missing face as not found, so the board holds still', () => {
    const g = toGazeInput({ x: NaN, y: NaN, trackingState: 3 }, 1920, 1080)
    expect(g).toMatchObject({ found: false, confidence: 0, state: 'FACE_MISSING' })
    expect(Number.isFinite(g.x) && Number.isFinite(g.y)).toBe(true)
  })

  it('never reports a point it has no numbers for', () => {
    expect(toGazeInput({ x: NaN, y: 10, trackingState: 0 }, 1920, 1080).found).toBe(false)
  })

  it('names an unknown state instead of guessing', () => {
    expect(toGazeInput({ x: 1, y: 1, trackingState: 9 }, 100, 100)).toMatchObject({ found: false, state: 'STATE_9' })
  })
})

describe('whyNotRunning', () => {
  it('runs in Auto and Gaze', () => {
    expect(whyNotRunning('auto', ok)).toBeNull()
    expect(whyNotRunning('gaze', ok)).toBeNull()
  })

  it('leaves Webcam to the head, and Scan / Head tilt / off without a camera', () => {
    for (const mode of ['webcam', 'scan', 'headtilt', 'off', null] as const) {
      expect(whyNotRunning(mode, ok)).toBe('not a gaze mode')
    }
  })

  it('never runs inside the tablet shell, which has its own Eyedid', () => {
    expect(whyNotRunning('auto', { ...ok, nativeShell: true })).toMatch(/tablet/)
  })

  it('needs a key, so the board runs exactly as before without one', () => {
    expect(whyNotRunning('auto', { ...ok, key: '' })).toMatch(/no key/)
  })

  it('needs a cross-origin isolated page for its threads', () => {
    expect(whyNotRunning('gaze', { ...ok, isolated: false })).toMatch(/isolated/)
  })
})
