// The desktop agent (docs/desktop-control.md) on this laptop. Its Eyedid worker owns the webcam, and
// it streams the gaze on ws://127.0.0.1:8766 (desktop/agent/bridge.py). While that link is up the
// board takes the gaze from it, as it does from the tablet shell (native.ts): Eyedid web does not
// start and the head camera stays off (cameraOwner.ts). Gaze stays on this laptop, never the Core.
//
// The agent sends physical pixels of the primary screen; this page turns them into fractions of its
// own window. That is exact in full screen (F11) at 100% page zoom; in a window it allows for the
// browser's frame and toolbar from outer / inner sizes.

import { gaze } from './gaze'

export const DESKTOP_GAZE_URL: string =
  (import.meta.env.VITE_DESKTOP_GAZE_URL as string | undefined)?.trim() || 'ws://127.0.0.1:8766'
const RETRY_MS = 3000

export interface WindowGeometry {
  screenX: number
  screenY: number
  outerWidth: number
  outerHeight: number
  innerWidth: number
  innerHeight: number
  devicePixelRatio: number
}

/**
 * A point on the screen (physical pixels) as fractions of this page's window, and whether it is on
 * the page at all (looking at another window must not move the board's highlight).
 */
export function screenToWindow(px: number, py: number, w: WindowGeometry): { x: number; y: number; inside: boolean } {
  const dpr = w.devicePixelRatio || 1
  // Browsers report window positions and sizes in CSS pixels. The frame is the same on the left,
  // right and bottom; everything else above the page is the toolbar.
  const side = Math.max(0, (w.outerWidth - w.innerWidth) / 2)
  const left = w.screenX + side
  const top = w.screenY + Math.max(0, w.outerHeight - w.innerHeight - side)
  const x = (px / dpr - left) / w.innerWidth
  const y = (py / dpr - top) / w.innerHeight
  return { x, y, inside: x >= 0 && x <= 1 && y >= 0 && y <= 1 }
}

type AgentMessage =
  | { type: 'hello'; screen: [number, number]; version: number }
  | { type: 'gaze'; t: number; x: number | null; y: number | null; found: boolean; state: string }

class DesktopAgentLink {
  private ws: WebSocket | null = null
  private retry: ReturnType<typeof setTimeout> | null = null
  private wanted = false
  private live = false
  private listeners = new Set<() => void>()

  /** The agent is connected: it owns the camera and feeds the gaze. */
  active = (): boolean => this.live

  subscribe = (fn: () => void) => {
    this.listeners.add(fn)
    return () => {
      this.listeners.delete(fn)
    }
  }

  /** Look for the agent (after "Click to start"), and keep looking every 3 s. */
  start = (): void => {
    if (this.wanted) return
    this.wanted = true
    this.connect()
  }

  stop = (): void => {
    this.wanted = false
    if (this.retry) clearTimeout(this.retry)
    this.retry = null
    this.ws?.close()
    this.ws = null
    this.setLive(false)
  }

  private setLive(on: boolean) {
    if (on === this.live) return
    this.live = on
    if (!on) gaze.clear()
    this.listeners.forEach((fn) => fn())
  }

  private connect() {
    if (!this.wanted || typeof WebSocket === 'undefined') return
    let ws: WebSocket
    try {
      ws = new WebSocket(DESKTOP_GAZE_URL)
    } catch {
      this.later()
      return
    }
    this.ws = ws
    ws.onmessage = (e) => this.onMessage(e.data)
    ws.onclose = () => {
      if (this.ws === ws) this.ws = null
      this.setLive(false)
      this.later()
    }
    ws.onerror = () => ws.close()
  }

  private later() {
    if (!this.wanted || this.retry) return
    this.retry = setTimeout(() => {
      this.retry = null
      this.connect()
    }, RETRY_MS)
  }

  private onMessage(data: unknown) {
    let msg: AgentMessage
    try {
      msg = JSON.parse(String(data)) as AgentMessage
    } catch {
      return
    }
    if (msg.type === 'hello') {
      this.setLive(true)
      return
    }
    if (msg.type !== 'gaze') return
    if (!msg.found || msg.x === null || msg.y === null) {
      gaze.feed({ x: 0.5, y: 0.5, found: false, confidence: 0, state: msg.state })
      return
    }
    const p = screenToWindow(msg.x, msg.y, window)
    // Looking at another window: alive, but the eyes are not on the board.
    gaze.feed({ x: p.x, y: p.y, found: p.inside, confidence: p.inside ? 1 : 0, state: p.inside ? msg.state : 'OFF_PAGE' })
  }
}

/** The one link on this page. */
export const desktopAgent = new DesktopAgentLink()
