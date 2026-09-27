import { useSyncExternalStore } from 'react'
import type { Lang, PointingMode, Screen } from '../contracts'
import { STRINGS } from '../board/strings'
import { nativeGazeActive, subscribeNativeGaze } from './native'
import { cursor, dwell, eyesLost, gazeConnected, showCursor } from './stores'
import { useTrackerStatus } from './useTrackerStatus'

/**
 * PRD section 11: a light on screen whenever the camera is on: the page's own head tracker, or the
 * tablet shell's eye tracker (which owns the camera while nativeGazeActive() is true).
 */
export function CameraLight({ lang }: { lang: Lang }) {
  const status = useTrackerStatus()
  const native = useSyncExternalStore(subscribeNativeGaze, nativeGazeActive)
  if (status.kind !== 'on' && status.kind !== 'starting' && !native) return null
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

/**
 * No eye tracker feeding the gaze slot while only the gaze can point: Gaze mode, or the tablet
 * shell (which owns the camera, so there is no head) in Auto or Webcam.
 */
export function GazeNotice({ lang, mode }: { lang: Lang; mode: PointingMode | null }) {
  const live = useSyncExternalStore(gazeConnected.subscribe, gazeConnected.get)
  const gazeOnly = mode === 'gaze' || (nativeGazeActive() && (mode === 'auto' || mode === 'webcam'))
  if (!gazeOnly || live) return null
  return (
    <p role="alert" className="max-w-xl rounded-2xl bg-amber-950/95 px-4 py-2 text-lg text-amber-100 ring-2 ring-amber-500">
      {STRINGS[lang].pointing.noGaze}
    </p>
  )
}

/** The gaze drives the highlight but the eye tracker does not see the eyes: small, not an alert. */
export function EyesNotice({ lang }: { lang: Lang }) {
  const lost = useSyncExternalStore(eyesLost.subscribe, eyesLost.get)
  if (!lost) return null
  return (
    <span className="rounded-full bg-zinc-800/95 px-4 py-1 text-xl font-semibold text-amber-200 ring-2 ring-amber-400/60">
      {STRINGS[lang].pointing.eyesLost}
    </span>
  )
}

/** Dwell select: a ring filling up over the highlighted tile until it is picked. */
export function DwellRing() {
  const d = useSyncExternalStore(dwell.subscribe, dwell.get)
  if (!d) return null
  const el = document.querySelector(`[data-tile-index="${d.tile}"]`)
  if (!el) return null
  const r = el.getBoundingClientRect()
  const size = Math.min(160, r.width * 0.5, r.height * 0.6)
  const radius = size / 2 - 8
  const length = 2 * Math.PI * radius
  return (
    <svg
      aria-hidden
      width={size}
      height={size}
      className="pointer-events-none fixed z-30 -rotate-90"
      style={{ left: r.left + r.width / 2 - size / 2, top: r.top + r.height / 2 - size / 2 }}
    >
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={10} className="stroke-zinc-700/70" />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        strokeWidth={10}
        strokeLinecap="round"
        strokeDasharray={length}
        strokeDashoffset={length * (1 - d.progress)}
        className="stroke-yellow-300"
      />
    </svg>
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
