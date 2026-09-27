import { describe, expect, it } from 'vitest'
import { screenToWindow, type WindowGeometry } from './desktopAgent'
import { whyNotRunning } from './eyedidWeb'

// A 1920 x 1080 laptop at 125% scaling: 1536 x 864 CSS pixels.
const fullScreen: WindowGeometry = {
  screenX: 0, screenY: 0, outerWidth: 1536, outerHeight: 864, innerWidth: 1536, innerHeight: 864, devicePixelRatio: 1.25,
}

describe('screenToWindow', () => {
  it('is exact in full screen', () => {
    expect(screenToWindow(960, 540, fullScreen)).toEqual({ x: 0.5, y: 0.5, inside: true })
    expect(screenToWindow(0, 1080, fullScreen)).toEqual({ x: 0, y: 1, inside: true })
  })

  it('allows for the browser frame and toolbar in a window', () => {
    // A window at (100, 50) CSS px, 8 px frame, 80 px of tabs and toolbar above the page.
    const w: WindowGeometry = {
      screenX: 100, screenY: 50, outerWidth: 1016, outerHeight: 696, innerWidth: 1000, innerHeight: 608, devicePixelRatio: 1.25,
    }
    const page = { left: 100 + 8, top: 50 + 80 }
    const p = screenToWindow((page.left + 250) * 1.25, (page.top + 152) * 1.25, w)
    expect(p.x).toBeCloseTo(0.25)
    expect(p.y).toBeCloseTo(0.25)
    expect(p.inside).toBe(true)
  })

  it('says when the eyes are on another window', () => {
    const w = { ...fullScreen, outerWidth: 768, innerWidth: 768 } // the board fills the left half
    expect(screenToWindow(1500, 500, w).inside).toBe(false)
  })
})

describe('whyNotRunning with the desktop agent', () => {
  const env = { key: 'k', nativeShell: false, isolated: true }
  it('leaves the camera to the agent', () => {
    expect(whyNotRunning('auto', { ...env, desktopAgent: true })).toBe('the desktop agent tracks the eyes')
    expect(whyNotRunning('auto', env)).toBeNull()
  })
})
