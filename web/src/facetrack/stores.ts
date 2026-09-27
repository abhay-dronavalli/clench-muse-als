// Two tiny shared stores for the board and the dev panel (read with useSyncExternalStore):
//
//   cursor         where the head or the gaze points right now (null = not seen / off), for the cursor dot
//   showCursor     the dev panel's "Cursor dot" toggle, remembered in this browser only
//   gazeConnected  an eye tracker is feeding the gaze slot (gaze.ts)

import type { ScreenPoint } from './pose'

type Listener = () => void

function store<T>(initial: T) {
  let value = initial
  const listeners = new Set<Listener>()
  return {
    get: () => value,
    set(next: T) {
      if (Object.is(next, value)) return
      value = next
      listeners.forEach((fn) => fn())
    },
    subscribe(fn: Listener) {
      listeners.add(fn)
      return () => {
        listeners.delete(fn)
      }
    },
  }
}

export const cursor = store<ScreenPoint | null>(null)

export const gazeConnected = store<boolean>(false)

const CURSOR_KEY = 'clench.cursorDot'

function readCursorPref(): boolean {
  try {
    return window.localStorage.getItem(CURSOR_KEY) !== 'off'
  } catch {
    return true // storage blocked: default on
  }
}

export const showCursor = store<boolean>(readCursorPref())

showCursor.subscribe(() => {
  try {
    window.localStorage.setItem(CURSOR_KEY, showCursor.get() ? 'on' : 'off')
  } catch {
    // a convenience only; the toggle still works for this page
  }
})
