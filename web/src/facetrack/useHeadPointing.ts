import { useEffect, useRef } from 'react'
import type { HeadRange, Screen } from '../contracts'
import type { Send } from '../lib/useSocket'
import { FaceDebouncer } from './face'
import { poseToPoint } from './pose'
import { cursor } from './stores'
import { chooseTile, type Rect } from './tiles'
import { tracker, type HeadSample } from './tracker'

interface Options {
  /** Camera on: the board is started and the pointing mode is Webcam or Auto (PRD section 11). */
  camera: boolean
  /** The board socket is open. On every (re)connect FACE_OK and the current tile are sent again. */
  connected: boolean
  /** The menu SCREEN on show, or null (confirm, speaking, help countdown: no tiles to point at). */
  screen: Screen | null
  send: Send
  range: HeadRange
  /** No POINTs (the calibration overlay is up); FACE_OK still goes out. */
  paused: boolean
}

/** FACE_OK debounce for the page's one tracker (like the tracker, one per page). */
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

/**
 * Webcam pointing on the board (PRD A3.3a). Runs the shared face tracker while `camera` is true,
 * and turns each frame into messages for the Core:
 *
 *   FACE_OK  when the face is seen or lost for 300 ms, on every (re)connect, and false when the
 *            camera stops or fails (so Auto falls back to scanning)
 *   POINT    the tile under the head's point on the screen (sticky edges), only when it changes,
 *            and once for every new SCREEN `seq`; only while a face is reported
 *
 * Video never leaves the browser: only these two messages are sent (PRD section 11).
 */
export function useHeadPointing({ camera, connected, screen, send, range, paused }: Options) {
  const latest = useRef({ screen, send, range, paused })
  useEffect(() => {
    latest.current = { screen, send, range, paused }
  })
  const tile = useRef<number | null>(null) // the tile the head is on (sticky), for this seq
  const sent = useRef<{ seq: number; tile: number } | null>(null)
  const seq = screen?.seq ?? null

  // New tiles: start from the Core's highlight (it keeps the same index for the head) and send
  // the tile under the head for this seq.
  useEffect(() => {
    tile.current = latest.current.screen?.highlight ?? null
    sent.current = null
  }, [seq])

  // Camera on only while wanted; off (the light goes out) as soon as the mode or page changes.
  useEffect(() => {
    if (!camera) return
    void tracker.start()
    return () => {
      tracker.stop()
      cursor.set(null)
      if (face.reported) latest.current.send({ type: 'FACE_OK', ok: false })
      face.reset()
      sent.current = null
    }
  }, [camera])

  // A camera that fails (denied, busy, unplugged) is a lost face.
  useEffect(
    () =>
      tracker.subscribeStatus(() => {
        if (tracker.status.kind !== 'error') return
        cursor.set(null)
        if (face.reported !== false) latest.current.send({ type: 'FACE_OK', ok: false })
        face.reset()
        face.reported = false
      }),
    [],
  )

  // After a (re)connect the Core may have restarted: tell it again what the camera sees.
  useEffect(() => {
    if (!connected || !camera) return
    if (face.reported !== null) latest.current.send({ type: 'FACE_OK', ok: face.reported })
    sent.current = null
  }, [connected, camera])

  useEffect(
    () =>
      tracker.subscribeSample((s: HeadSample) => {
        const { screen, send, range, paused } = latest.current
        const change = face.update(s.face, s.t)
        if (change !== null) {
          send({ type: 'FACE_OK', ok: change })
          sent.current = null // a face is back: send the tile it points at, even if unchanged
        }
        if (!s.angles) {
          cursor.set(null)
          return
        }
        const p = poseToPoint(s.angles, range)
        cursor.set(p)
        if (paused || face.reported !== true || !screen || screen.tiles.length === 0) return
        const rects = measureTiles()
        if (rects.length !== screen.tiles.length) return // the new tiles are not drawn yet
        const next = chooseTile(p, rects, tile.current)
        tile.current = next
        if (next === null) return
        if (sent.current?.seq === screen.seq && sent.current.tile === next) return
        if (send({ type: 'POINT', source: 'webcam', tile: next, seq: screen.seq, t: Date.now() / 1000 })) {
          sent.current = { seq: screen.seq, tile: next }
        }
      }),
    [],
  )
}
