import { useEffect, useRef } from 'react'
import type { HeadRange, PointingMode, Screen } from '../contracts'
import type { Send } from '../lib/useSocket'
import { FaceDebouncer } from './face'
import { gaze } from './gaze'
import { gazePointerTuning } from './gazeTuning'
import { gazeOwnsCamera } from './cameraOwner'
import { chooseSource, fromHead, POINT_SOURCE, type PointSample, type SourceName } from './source'
import { cursor, dwell, eyesLost, gazeConnected, gazeTuning } from './stores'
import { DwellTimer, HEAD_TUNING, TilePointer } from './tilePointer'
import { STICKY_MARGIN, type Rect } from './tiles'
import { tracker } from './tracker'

interface Options {
  /** The pointing mode from SETTINGS (null before the first one). */
  mode: PointingMode | null
  /** The board was started ("Click to start"); nothing runs before. */
  started: boolean
  /** The board socket is open. On every (re)connect FACE_OK and the current tile are sent again. */
  connected: boolean
  /** The menu SCREEN on show, or null (confirm, speaking, help countdown: no tiles to point at). */
  screen: Screen | null
  send: Send
  range: HeadRange
  /** No POINTs (the calibration overlay is up); FACE_OK still goes out. */
  paused: boolean
  /** Sticky edges: share of a tile's size the point must be inside it (SETTINGS tile_switch_margin). */
  margin?: number
  /**
   * Dwell select (gaze only, gazeTuning.dwell): pick the highlighted tile of menu screen `seq`, the
   * same as a clench. Only called on a menu screen; the board checks again that screen `seq` is still
   * the one on show (this hook's `screen` can be a render behind the Core's messages).
   */
  pick?: (seq: number) => boolean
}

/** How often the gaze slot is checked for an eye tracker that stopped feeding points. */
const GAZE_WATCH_MS = 200

/** FACE_OK debounce for the page's one pointer (like the tracker, one per page). */
const face = new FaceDebouncer()

/** Every tile's box as fractions of the window, from elements carrying `attr` (the board's `data-tile-index`). */
export function measureTiles(attr = 'data-tile-index'): Rect[] {
  const w = window.innerWidth
  const h = window.innerHeight
  const key = (el: Element) => Number(el.getAttribute(attr))
  return [...document.querySelectorAll<HTMLElement>(`[${attr}]`)]
    .sort((a, b) => key(a) - key(b))
    .map((el) => {
      const r = el.getBoundingClientRect()
      return { left: r.left / w, top: r.top / h, right: r.right / w, bottom: r.bottom / h }
    })
}

/** The board points (sends POINT / FACE_OK) in this mode: Webcam, Gaze, Auto. */
export function boardPoints(mode: PointingMode | null): boolean {
  return chooseSource(mode, false) !== null
}

/**
 * The head tracker (and so the camera, with its light) runs in this mode: Webcam, Auto. Never while
 * an eye tracker owns the camera (the tablet shell's or Eyedid web, cameraOwner.ts).
 */
export function headCamera(mode: PointingMode | null): boolean {
  return (mode === 'webcam' || mode === 'auto') && !gazeOwnsCamera()
}

/**
 * Pointing on the board (PRD A3.3a) from a pluggable screen-point source (source.ts): the head
 * (MediaPipe head pose, tracker.ts) or the gaze (an eye tracker feeding gaze.ts). Webcam mode uses
 * the head, Gaze mode the gaze, Auto the gaze while it is available, else the head. The active
 * source's samples go through one TilePointer (tilePointer.ts; for gaze a One Euro filter and a
 * 300 ms hold before the highlight moves) and become messages for the Core:
 *
 *   FACE_OK  when the person is seen or lost for 300 ms (face for head, eyes for gaze), on every
 *            (re)connect, and false when the camera stops or fails or the eye tracker stops
 *   POINT    the highlighted tile, source "webcam" or "gaze", only when it changes, and once for
 *            every new SCREEN `seq`; only while the person is seen
 *
 * Video never leaves the browser: only these two messages are sent (PRD section 11). Dwell select,
 * when on, calls `pick` (the board sends CLENCH).
 */
export function usePointing({ mode, started, connected, screen, send, range, paused, margin = STICKY_MARGIN, pick }: Options) {
  const pointing = started && boardPoints(mode)
  const camera = started && headCamera(mode)
  const latest = useRef({ mode, pointing, screen, send, range, paused, margin, pick })
  useEffect(() => {
    latest.current = { mode, pointing, screen, send, range, paused, margin, pick }
  })
  const pointer = useRef(new TilePointer(HEAD_TUNING)) // the highlighted tile (sticky, held), for this seq
  const dwellTimer = useRef(new DwellTimer())
  const sent = useRef<{ seq: number; tile: number; source: SourceName } | null>(null)
  const active = useRef<SourceName | null>(null)
  const seq = screen?.seq ?? null

  // New tiles: start from the Core's highlight (it keeps the same index while pointing) and send
  // the tile under the point for this seq.
  useEffect(() => {
    pointer.current.reset(latest.current.screen?.highlight ?? null)
    dwellTimer.current.reset()
    dwell.set(null)
    sent.current = null
  }, [seq])

  // Camera on only while the head is wanted; off (the light goes out) as soon as the mode changes.
  useEffect(() => {
    if (!camera) return
    void tracker.start()
    return () => {
      tracker.stop()
      if (active.current === 'head') cursor.set(null)
    }
  }, [camera])

  // Pointing off (Scan, Head tilt): the Core hears that nobody is seen.
  useEffect(() => {
    if (!pointing) return
    return () => {
      cursor.set(null)
      eyesLost.set(false)
      dwell.set(null)
      if (face.reported) latest.current.send({ type: 'FACE_OK', ok: false })
      face.reset()
      sent.current = null
      active.current = null
    }
  }, [pointing])

  // A camera that fails (denied, busy, unplugged) is a lost face while the head is pointing.
  useEffect(
    () =>
      tracker.subscribeStatus(() => {
        const source = chooseSource(latest.current.mode, gaze.available(), !gazeOwnsCamera())
        if (tracker.status.kind !== 'error' || source !== 'head') return
        cursor.set(null)
        if (face.reported !== false) latest.current.send({ type: 'FACE_OK', ok: false })
        face.reset()
        face.reported = false
      }),
    [],
  )

  // After a (re)connect the Core may have restarted: tell it again what the board sees.
  useEffect(() => {
    if (!connected || !pointing) return
    if (face.reported !== null) latest.current.send({ type: 'FACE_OK', ok: face.reported })
    sent.current = null
  }, [connected, pointing])

  // Every sample from either source; only the active one drives the highlight.
  useEffect(() => {
    const stopDwell = () => {
      dwellTimer.current.reset()
      dwell.set(null)
    }
    const onSample = (s: PointSample) => {
      const { mode, pointing, screen, send, paused, margin, pick } = latest.current
      if (!pointing || s.source !== chooseSource(mode, gaze.available(s.t), !gazeOwnsCamera())) return
      const p = pointer.current
      if (active.current !== s.source) {
        active.current = s.source
        p.lost() // the other source's filter state means nothing here
        sent.current = null // tell the Core where this source points, even on the same tile
        stopDwell()
      }
      p.tuning = s.source === 'gaze' ? gazePointerTuning(gazeTuning.get(), margin) : { ...HEAD_TUNING, margin }
      // A live tracker that does not see the eyes (a stopped one gets "no eye tracker" instead).
      eyesLost.set(s.source === 'gaze' && !s.found && gaze.connected(s.t))
      const change = face.update(s.found, s.t)
      if (change !== null) {
        send({ type: 'FACE_OK', ok: change })
        sent.current = null // seen again: send the tile it points at, even if unchanged
      }
      if (!s.point || !s.found) {
        cursor.set(s.point)
        p.lost()
        stopDwell()
        return
      }
      const expected = screen ? screen.tiles.length + (screen.corner ? 1 : 0) : 0 // the corner button is the last index
      if (paused || face.reported !== true || !screen || screen.tiles.length === 0) {
        cursor.set(s.point)
        stopDwell()
        return
      }
      const rects = measureTiles()
      if (rects.length !== expected) return // the new tiles are not drawn yet
      const step = p.update(s.point, s.t, rects)
      cursor.set(step.point)
      const next = step.tile
      if (next === null) return
      if (!(sent.current?.seq === screen.seq && sent.current.tile === next && sent.current.source === s.source)) {
        const source = POINT_SOURCE[s.source]
        if (send({ type: 'POINT', source, tile: next, seq: screen.seq, t: Date.now() / 1000 })) {
          sent.current = { seq: screen.seq, tile: next, source: s.source }
        }
      }
      // Dwell select: gaze only, on a menu screen that is not loading, once the Core has this tile.
      const tuning = gazeTuning.get()
      const canDwell = tuning.dwell && pick && s.source === 'gaze' && !screen.loading && sent.current?.tile === next
      if (!canDwell) {
        stopDwell()
        return
      }
      dwellTimer.current.dwellMs = tuning.dwellMs
      const d = dwellTimer.current.update(next, s.t)
      dwell.set(d.progress > 0 ? { tile: next, progress: d.progress } : null)
      if (d.fire) {
        dwell.set(null)
        pick(screen.seq)
      }
    }
    const offHead = tracker.subscribeSample((h) => onSample(fromHead(h, latest.current.range)))
    const offGaze = gaze.subscribe(onSample)
    // An eye tracker that stops feeding points: "not seen" while the gaze is the active source
    // (Auto moves to the head with its next frame, if there is one), and the board's notice.
    const watch = window.setInterval(() => {
      const now = performance.now()
      const live = gaze.connected(now)
      gazeConnected.set(live)
      if (live) return
      const { mode } = latest.current
      if (mode === 'gaze' || (gazeOwnsCamera() && chooseSource(mode, false, false) === 'gaze')) {
        onSample({ source: 'gaze', t: now, found: false, point: null, confidence: 0 })
      }
    }, GAZE_WATCH_MS)
    return () => {
      offHead()
      offGaze()
      window.clearInterval(watch)
    }
  }, [])
}
