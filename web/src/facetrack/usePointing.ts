import { useEffect, useRef } from 'react'
import type { HeadRange, PointingMode, Screen } from '../contracts'
import type { Send } from '../lib/useSocket'
import { FaceDebouncer } from './face'
import { gaze } from './gaze'
import { chooseSource, fromHead, POINT_SOURCE, type PointSample, type SourceName } from './source'
import { cursor, gazeConnected } from './stores'
import { chooseTile, STICKY_MARGIN, type Rect } from './tiles'
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
}

/** How often the gaze slot is checked for an eye tracker that stopped feeding points. */
const GAZE_WATCH_MS = 200

/** FACE_OK debounce for the page's one pointer (like the tracker, one per page). */
const face = new FaceDebouncer()

/** Every tile's box as fractions of the window, from the board's `data-tile-index` elements. */
function measureTiles(): Rect[] {
  const w = window.innerWidth
  const h = window.innerHeight
  return [...document.querySelectorAll<HTMLElement>('[data-tile-index]')]
    .sort((a, b) => Number(a.dataset.tileIndex) - Number(b.dataset.tileIndex))
    .map((el) => {
      const r = el.getBoundingClientRect()
      return { left: r.left / w, top: r.top / h, right: r.right / w, bottom: r.bottom / h }
    })
}

/** The board points (sends POINT / FACE_OK) in this mode: Webcam, Gaze, Auto. */
export function boardPoints(mode: PointingMode | null): boolean {
  return chooseSource(mode, false) !== null
}

/** The head tracker (and so the camera, with its light) runs in this mode: Webcam, Auto. */
export function headCamera(mode: PointingMode | null): boolean {
  return mode === 'webcam' || mode === 'auto'
}

/**
 * Pointing on the board (PRD A3.3a) from a pluggable screen-point source (source.ts): the head
 * (MediaPipe head pose, tracker.ts) or the gaze (an eye tracker feeding gaze.ts). Webcam mode uses
 * the head, Gaze mode the gaze, Auto the gaze while it is available, else the head. The active
 * source's samples become messages for the Core:
 *
 *   FACE_OK  when the person is seen or lost for 300 ms (face for head, eyes for gaze), on every
 *            (re)connect, and false when the camera stops or fails or the eye tracker stops
 *   POINT    the tile under the point (sticky edges), source "webcam" or "gaze", only when it
 *            changes, and once for every new SCREEN `seq`; only while the person is seen
 *
 * Video never leaves the browser: only these two messages are sent (PRD section 11).
 */
export function usePointing({ mode, started, connected, screen, send, range, paused, margin = STICKY_MARGIN }: Options) {
  const pointing = started && boardPoints(mode)
  const camera = started && headCamera(mode)
  const latest = useRef({ mode, pointing, screen, send, range, paused, margin })
  useEffect(() => {
    latest.current = { mode, pointing, screen, send, range, paused, margin }
  })
  const tile = useRef<number | null>(null) // the tile the point is on (sticky), for this seq
  const sent = useRef<{ seq: number; tile: number; source: SourceName } | null>(null)
  const active = useRef<SourceName | null>(null)
  const seq = screen?.seq ?? null

  // New tiles: start from the Core's highlight (it keeps the same index while pointing) and send
  // the tile under the point for this seq.
  useEffect(() => {
    tile.current = latest.current.screen?.highlight ?? null
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
        if (tracker.status.kind !== 'error' || chooseSource(latest.current.mode, gaze.available()) !== 'head') return
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
    const onSample = (s: PointSample) => {
      const { mode, pointing, screen, send, paused, margin } = latest.current
      if (!pointing || s.source !== chooseSource(mode, gaze.available(s.t))) return
      if (active.current !== s.source) {
        active.current = s.source
        sent.current = null // tell the Core where this source points, even on the same tile
      }
      const change = face.update(s.found, s.t)
      if (change !== null) {
        send({ type: 'FACE_OK', ok: change })
        sent.current = null // seen again: send the tile it points at, even if unchanged
      }
      cursor.set(s.point)
      if (!s.point || !s.found) return
      if (paused || face.reported !== true || !screen || screen.tiles.length === 0) return
      const rects = measureTiles()
      if (rects.length !== screen.tiles.length) return // the new tiles are not drawn yet
      const next = chooseTile(s.point, rects, tile.current, margin)
      tile.current = next
      if (next === null) return
      if (sent.current?.seq === screen.seq && sent.current.tile === next && sent.current.source === s.source) return
      const source = POINT_SOURCE[s.source]
      if (send({ type: 'POINT', source, tile: next, seq: screen.seq, t: Date.now() / 1000 })) {
        sent.current = { seq: screen.seq, tile: next, source: s.source }
      }
    }
    const offHead = tracker.subscribeSample((h) => onSample(fromHead(h, latest.current.range)))
    const offGaze = gaze.subscribe(onSample)
    // An eye tracker that stops feeding points: "not seen" in Gaze mode (Auto moves to the head
    // with its next frame), and the board's "no eye tracker" notice.
    const watch = window.setInterval(() => {
      const now = performance.now()
      gazeConnected.set(gaze.connected(now))
      if (latest.current.mode === 'gaze' && !gaze.connected(now)) {
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
