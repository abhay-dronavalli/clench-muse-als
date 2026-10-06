import { beforeEach, describe, expect, it, vi } from 'vitest'
import { attachCarTarget, browserCar, resetCarHub, type CarTarget } from './carHub'

function fakeTarget(): CarTarget & { calls: unknown[][] } {
  const calls: unknown[][] = []
  return {
    calls,
    show: (on) => calls.push(['show', on]),
    setLayout: (m) => calls.push(['setLayout', m]),
    setSpeed: (mph) => calls.push(['setSpeed', mph]),
    play: (a, ms, w) => calls.push(['play', a, ms, w]),
    setPreview: (on) => calls.push(['setPreview', on]),
  }
}

describe('the browser car hub', () => {
  beforeEach(() => resetCarHub())

  it('does nothing before a renderer attaches (no crash)', () => {
    expect(() => {
      browserCar.show(true)
      browserCar.setSpeed(20)
      browserCar.play('warmer', 900, '')
    }).not.toThrow()
  })

  it('replays what the page asked for when the renderer attaches', () => {
    browserCar.show(true)
    browserCar.setLayout('split')
    browserCar.setSpeed(18)
    browserCar.setPreview(true)
    const t = fakeTarget()
    attachCarTarget(t)
    expect(t.calls).toEqual([
      ['setLayout', 'split'],
      ['setSpeed', 18],
      ['setPreview', true],
      ['show', true],
    ])
  })

  it('passes later calls straight through, and effects are not replayed', () => {
    const t = fakeTarget()
    attachCarTarget(t)
    t.calls.length = 0
    browserCar.play('window_up', 1200, 'front_left')
    browserCar.setSpeed(0)
    expect(t.calls).toEqual([
      ['play', 'window_up', 1200, 'front_left'],
      ['setSpeed', 0],
    ])
  })

  it('a detached renderer gets no more calls', () => {
    const t = fakeTarget()
    const detach = attachCarTarget(t)
    detach()
    t.calls.length = 0
    browserCar.show(false)
    expect(t.calls).toEqual([])
  })

  it('a second renderer takes over, and the first one detaching does not drop it', () => {
    const first = fakeTarget()
    const second = fakeTarget()
    const detachFirst = attachCarTarget(first)
    attachCarTarget(second)
    detachFirst()
    const spy = vi.fn()
    second.show = spy
    browserCar.show(true)
    expect(spy).toHaveBeenCalledWith(true)
  })
})
