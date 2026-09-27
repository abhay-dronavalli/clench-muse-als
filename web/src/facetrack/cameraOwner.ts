// One camera owner. While an eye tracker owns the webcam (the tablet shell's Eyedid, native.ts,
// Eyedid web on the laptop, eyedidWeb.ts, or the desktop agent's Eyedid, desktopAgent.ts) the board
// never opens it for the head: Auto follows the gaze or scans, as it always has in the tablet shell.

import { desktopAgent } from './desktopAgent'
import { eyedidWeb } from './eyedidWeb'
import { nativeGazeActive, subscribeNativeGaze } from './native'

export function gazeOwnsCamera(): boolean {
  return nativeGazeActive() || eyedidWeb.active() || desktopAgent.active()
}

/** For useSyncExternalStore(subscribeGazeOwner, gazeOwnsCamera). */
export function subscribeGazeOwner(fn: () => void) {
  const offNative = subscribeNativeGaze(fn)
  const offWeb = eyedidWeb.subscribe(fn)
  const offDesktop = desktopAgent.subscribe(fn)
  return () => {
    offNative()
    offWeb()
    offDesktop()
  }
}
