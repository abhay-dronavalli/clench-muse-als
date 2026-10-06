import { describe, expect, it } from 'vitest'
import * as WorldPlan from './worldPlan'

describe('the world plan', () => {
  it('is the same for the same seed and different for another', () => {
    expect(WorldPlan.plan(7)).toEqual(WorldPlan.plan(7))
    expect(WorldPlan.plan(7)).not.toEqual(WorldPlan.plan(8))
  })

  it('keeps tall things out of the camera circle, for many seeds', () => {
    for (let seed = 1; seed <= 50; seed++) {
      expect(WorldPlan.keepsClear(WorldPlan.plan(seed))).toBe(true)
    }
  })

  it('keeps flowers and rocks beside the road, not on it', () => {
    for (const p of WorldPlan.plan(3).filter((q) => q.kind === 'FLOWER' || q.kind === 'ROCK')) {
      const inside = p.z > WorldPlan.ROAD_LEFT && p.z < WorldPlan.ROAD_RIGHT
      expect(inside).toBe(false)
    }
  })

  it('re-rolls a piece that wraps behind the car, and keeps it clear', () => {
    const rng = new WorldPlan.Rng(11)
    const tree = WorldPlan.plan(11).find((p) => p.kind === 'ROUND_TREE')!
    const before = WorldPlan.lap(tree, 0)
    const wrapped = WorldPlan.lap(tree, tree.span) // a full span further on
    expect(wrapped).not.toBe(before)
    WorldPlan.reroll(tree, rng)
    expect(Math.abs(tree.z)).toBeGreaterThan(WorldPlan.CAMERA_RADIUS)
  })
})

describe('Rng', () => {
  it('gives the same numbers for the same seed, all in range', () => {
    const a = new WorldPlan.Rng(42)
    const b = new WorldPlan.Rng(42)
    for (let i = 0; i < 200; i++) {
      const x = a.nextFloat()
      expect(x).toBe(b.nextFloat())
      expect(x).toBeGreaterThanOrEqual(0)
      expect(x).toBeLessThan(1)
    }
    for (let i = 0; i < 50; i++) expect(a.nextInt(4)).toBeLessThan(4)
  })
})
