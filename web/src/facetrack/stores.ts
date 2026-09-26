// Two tiny shared stores for the board and the dev panel (read with useSyncExternalStore):
//
//   cursor      where the head points right now (null = no face / camera off), for the cursor dot
//   showCursor  the dev panel's "Cursor dot" toggle, remembered in this browser only

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
