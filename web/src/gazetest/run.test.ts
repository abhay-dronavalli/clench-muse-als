import { describe, expect, it } from 'vitest'
import { DEFAULT_GAZE_TUNING } from '../facetrack/gazeTuning'
import { byPerson, makeRecord, toCsv } from './log'
import { GAP_MS, pickTarget, summarize, TestRun, TIMEOUT_MS, type Trial } from './run'

describe('pickTarget', () => {
  it('never picks an avoided tile', () => {
    for (let seed = 0; seed < 50; seed++) {
      let x = seed + 1
      const rnd = () => ((x = (x * 16807) % 2147483647) / 2147483647)
      const t = pickTarget(6, [3, 5], rnd)
      expect([0, 1, 2, 4]).toContain(t)
    }
    expect(pickTarget(6, [null, 2], () => 0)).toBe(0)
  })
})

describe('TestRun', () => {
  it('a hit when the highlight lands on the target, timed from the prompt', () => {
    const r = new TestRun(2, 6, 0, () => 0.4) // choices [1..5] minus avoided: index 2
    r.update(GAP_MS - 1, 0)
    expect(r.target).toBeNull()
    r.update(GAP_MS, 0) // prompt: not tile 0 (highlighted) -> [1,2,3,4,5][2] = 3
    expect(r.target).toBe(3)
    r.update(GAP_MS + 100, 1) // wrong tile on the way
    r.update(GAP_MS + 450, 3)
    expect(r.trials[0]).toEqual({ target: 3, hit: true, ms: 450, wrong: 1 })
  })

  it('a miss after the timeout, also without samples; done after the last target', () => {
    const r = new TestRun(1, 6, 0, () => 0)
    r.update(GAP_MS, 0) // target: first tile that is not 0 -> 1
    r.update(GAP_MS + TIMEOUT_MS, null)
    expect(r.trials[0]).toEqual({ target: 1, hit: false, ms: null, wrong: 0 })
    expect(r.done).toBe(true)
  })

  it('after a miss, the next target is never the tile the highlight rests on', () => {
    const r = new TestRun(2, 6, 0, () => 0.7)
    r.update(GAP_MS, 0) // [1,2,3,4,5][3] = 4
    expect(r.target).toBe(4)
    r.update(GAP_MS + 500, 3) // the eyes went to 3 instead, and stay there
    r.update(GAP_MS + TIMEOUT_MS, 3) // miss
    expect(r.trials[0]).toMatchObject({ target: 4, hit: false })
    const next = GAP_MS + TIMEOUT_MS + GAP_MS
    r.update(next, 3) // new prompt while tile 3 is highlighted
    // Avoiding only the previous target would pick [0,1,2,3,5][3] = 3 here: a free hit.
    expect(r.target).not.toBe(3)
    expect(r.target).not.toBe(4)
    expect(r.target).toBe(2) // [0,1,2,5][2]
    r.update(next + 100, 3)
    expect(r.trials).toHaveLength(1) // still on 3: no free hit
  })

  it('never starts a run on the tile already highlighted', () => {
    for (let i = 0; i < 6; i++) {
      const r = new TestRun(1, 6, 0, () => 0.99)
      r.update(GAP_MS, i)
      expect(r.target).not.toBe(i)
    }
  })

  it('summarizes: hit rate, mean time over hits, pass at 90%', () => {
    const hit = (ms: number): Trial => ({ target: 0, hit: true, ms, wrong: 0 })
    const miss: Trial = { target: 0, hit: false, ms: null, wrong: 2 }
    const s = summarize([...Array(9)].map(() => hit(400)).concat(miss))
    expect(s).toEqual({ trials: 10, hits: 9, hitRate: 0.9, avgMs: 400, wrong: 2, pass: true })
    expect(summarize([hit(400), miss]).pass).toBe(false)
    expect(summarize([]).avgMs).toBeNull()
  })
})

describe('log', () => {
  const rec = (person: string, hits: number) =>
    makeRecord({
      person,
      at: '2026-09-26T12:00:00Z',
      source: 'gaze',
      layout: '3x2',
      tuning: DEFAULT_GAZE_TUNING,
      margin: 0.05,
      sdkFilter: true,
      blinks: 3,
      trials: [...Array(10)].map((_, i) => ({ target: 0, hit: i < hits, ms: i < hits ? 500 : null, wrong: 0 })),
    })

  it('groups per person with the best run', () => {
    const stats = byPerson([rec('Ana', 7), rec('Ana', 10), rec('Ben', 8)])
    expect(stats.map((s) => [s.person, s.runs, s.best])).toEqual([['Ana', 2, 1], ['Ben', 1, 0.8]])
    expect(stats[0].hitRate).toBeCloseTo(0.85)
  })

  it('CSV: a header and one quoted-safe row per run', () => {
    const csv = toCsv([rec('O"Neil, J', 9)]).trim().split('\n')
    expect(csv).toHaveLength(2)
    expect(csv[0].startsWith('at,person,source')).toBe(true)
    expect(csv[1]).toContain('"O""Neil, J"')
    expect(csv[1]).toContain(',0.90,500,')
  })
})
