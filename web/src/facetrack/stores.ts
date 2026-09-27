// Tiny shared stores for the board and the dev panel (read with useSyncExternalStore):
//
//   cursor         where the head or the gaze points right now (null = not seen / off), for the cursor dot
//   showCursor     the dev panel's "Cursor dot" toggle, remembered in this browser only
//   gazeConnected  an eye tracker is feeding the gaze slot (gaze.ts)
//   eyesLost       the gaze drives the highlight but the tracker does not see the eyes right now
//   dwell          the dwell-select ring: which tile and how far along (null = no ring)
//   gazeTuning     filter, hold and dwell settings for gaze (gazeTuning.ts), remembered in this browser

import { DEFAULT_GAZE_TUNING, parseTuning, type GazeTuning } from './gazeTuning'
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

export const eyesLost = store<boolean>(false)

export const dwell = store<{ tile: number; progress: number } | null>(null)

const TUNING_KEY = 'clench.gazeTuning'

function readTuning(): GazeTuning {
  try {
    return parseTuning(window.localStorage.getItem(TUNING_KEY))
  } catch {
    return DEFAULT_GAZE_TUNING
  }
}

export const gazeTuning = store<GazeTuning>(readTuning())

gazeTuning.subscribe(() => {
  try {
    window.localStorage.setItem(TUNING_KEY, JSON.stringify(gazeTuning.get()))
  } catch {
    // a convenience only; the settings still apply to this page
  }
})

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
