import { describe, expect, it } from 'vitest'
import { FaceDebouncer } from './face'

describe('FaceDebouncer', () => {
  it('reports the first state once it has held', () => {
    const d = new FaceDebouncer(300)
    expect(d.update(true, 0)).toBeNull()
    expect(d.update(true, 200)).toBeNull()
    expect(d.update(true, 300)).toBe(true)
    expect(d.update(true, 400)).toBeNull() // no change: nothing to send
  })

  it('ignores a short dropout', () => {
    const d = new FaceDebouncer(300)
    d.update(true, 0)
    d.update(true, 300)
    expect(d.update(false, 400)).toBeNull()
    expect(d.update(false, 600)).toBeNull()
    expect(d.update(true, 650)).toBeNull()
    expect(d.update(true, 1000)).toBeNull()
    expect(d.reported).toBe(true)
  })

  it('reports a lost face after the debounce, and its return', () => {
    const d = new FaceDebouncer(300)
    d.update(true, 0)
    d.update(true, 300)
    d.update(false, 1000)
    expect(d.update(false, 1300)).toBe(false)
    d.update(true, 2000)
    expect(d.update(true, 2299)).toBeNull()
    expect(d.update(true, 2300)).toBe(true)
  })

  it('reports again after a reset', () => {
    const d = new FaceDebouncer(300)
    d.update(true, 0)
    d.update(true, 300)
    d.reset()
    expect(d.reported).toBeNull()
    d.update(true, 400)
    expect(d.update(true, 700)).toBe(true)
  })
})
