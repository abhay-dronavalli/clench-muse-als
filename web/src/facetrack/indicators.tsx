import { useSyncExternalStore } from 'react'
import type { Lang, PointingMode, Screen } from '../contracts'
import { STRINGS } from '../board/strings'
import { cursor, gazeConnected, showCursor } from './stores'
import { useTrackerStatus } from './useTrackerStatus'

/** PRD section 11: a light on screen whenever the camera is on. */
export function CameraLight({ lang }: { lang: Lang }) {
  const status = useTrackerStatus()
  if (status.kind !== 'on' && status.kind !== 'starting') return null
  return (
    <span className="flex items-center gap-2 rounded-full bg-zinc-900/90 px-3 py-1 text-base font-semibold text-zinc-200 ring-1 ring-red-500/60">
      <span className="h-3 w-3 animate-pulse rounded-full bg-red-500" aria-hidden />
      {STRINGS[lang].pointing.cameraOn}
    </span>
  )
}

/**
 * "Scanning" while Auto has fallen back (or Head tilt, which is not built yet, scans). Nothing in
 * Scan mode (scanning is what was asked for) or while the head drives the highlight.
 */
export function PointerBadge({ screen, mode }: { screen: Screen; mode: PointingMode | null }) {
  if (screen.pointer !== 'scan' || (mode !== 'auto' && mode !== 'headtilt')) return null
  const s = STRINGS[screen.lang].pointing
  return (
    <span className="rounded-full bg-zinc-800/95 px-4 py-1 text-xl font-semibold text-sky-200 ring-2 ring-sky-400/60">
      {mode === 'headtilt' ? s.headtilt : s.scanning}
    </span>
  )
}

/** A camera problem in plain words, and what happens meanwhile (Auto scans; Webcam waits). */
export function CameraNotice({ lang, mode }: { lang: Lang; mode: PointingMode | null }) {
  const status = useTrackerStatus()
  if (status.kind !== 'error' || (mode !== 'auto' && mode !== 'webcam')) return null
  const s = STRINGS[lang].camera
  return (
    <p role="alert" className="max-w-xl rounded-2xl bg-red-950/95 px-4 py-2 text-lg text-red-100 ring-2 ring-red-500">
      {s[status.problem]} {mode === 'auto' ? s.scanning : s.switch}
    </p>
  )
}

/** Gaze mode with no eye tracker feeding the gaze slot: the highlight cannot move. */
export function GazeNotice({ lang, mode }: { lang: Lang; mode: PointingMode | null }) {
  const live = useSyncExternalStore(gazeConnected.subscribe, gazeConnected.get)
  if (mode !== 'gaze' || live) return null
  return (
    <p role="alert" className="max-w-xl rounded-2xl bg-amber-950/95 px-4 py-2 text-lg text-amber-100 ring-2 ring-amber-500">
      {STRINGS[lang].pointing.noGaze}
    </p>
  )
}

/** A subtle dot where the head or the gaze is pointing (dev panel toggle). */
export function CursorDot() {
  const point = useSyncExternalStore(cursor.subscribe, cursor.get)
  const on = useSyncExternalStore(showCursor.subscribe, showCursor.get)
  if (!on || point === null) return null
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed z-30 h-7 w-7 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-300/50 ring-2 ring-sky-200/70"
      style={{ left: `${point.x * 100}%`, top: `${point.y * 100}%` }}
    />
  )
}
