// One camera owner. While an eye tracker owns the webcam (the tablet shell's Eyedid, native.ts, or
// Eyedid web on the laptop, eyedidWeb.ts) the board never opens it for the head: Auto follows the gaze
// or scans, exactly as it always has inside the tablet shell.

import { eyedidWeb } from './eyedidWeb'
import { nativeGazeActive, subscribeNativeGaze } from './native'

export function gazeOwnsCamera(): boolean {
  return nativeGazeActive() || eyedidWeb.active()
}

/** For useSyncExternalStore(subscribeGazeOwner, gazeOwnsCamera). */
export function subscribeGazeOwner(fn: () => void) {
  const offNative = subscribeNativeGaze(fn)
  const offWeb = eyedidWeb.subscribe(fn)
  return () => {
    offNative()
    offWeb()
  }
}
