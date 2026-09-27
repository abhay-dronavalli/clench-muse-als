import { describe, expect, it } from 'vitest'
import { DEFAULT_GAZE_TUNING, gazePointerTuning, parseTuning } from './gazeTuning'

describe('parseTuning', () => {
  it('nothing saved, or broken JSON: the defaults (dwell off)', () => {
    expect(parseTuning(null)).toEqual(DEFAULT_GAZE_TUNING)
    expect(parseTuning('{not json')).toEqual(DEFAULT_GAZE_TUNING)
    expect(DEFAULT_GAZE_TUNING.dwell).toBe(false)
    expect(DEFAULT_GAZE_TUNING.holdMs).toBe(300)
  })

  it('keeps valid fields, clamps numbers, ignores wrong types', () => {
    const t = parseTuning(JSON.stringify({ oneEuro: false, holdMs: 5000, dwell: 'yes', euro: { beta: 3 } }))
    expect(t.oneEuro).toBe(false)
    expect(t.holdMs).toBe(1000)
    expect(t.dwell).toBe(false)
    expect(t.euro).toEqual({ ...DEFAULT_GAZE_TUNING.euro, beta: 3 })
  })

  it('makes pointer settings with the given margin', () => {
    expect(gazePointerTuning(DEFAULT_GAZE_TUNING, 0.1)).toMatchObject({ holdMs: 300, margin: 0.1, oneEuro: true })
  })
})
