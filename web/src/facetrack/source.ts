// Pluggable screen-point sources for pointing (PRD D2, A3.3a). Pure, unit tested.
//
//   head  the MediaPipe head pose from tracker.ts, mapped with the calibrated head range (pose.ts)
//   gaze  an eye tracker feeding gaze.ts (docs/eye-tracking.md)
//
// Both become the same PointSample; usePointing turns the active one into POINT and FACE_OK.

import type { HeadRange, PointingMode, PointSource } from '../contracts'
import { poseToPoint, type ScreenPoint } from './pose'
import type { HeadSample } from './tracker'

export type SourceName = 'head' | 'gaze'

export interface PointSample {
  source: SourceName
  /** performance.now() of the sample, ms */
  t: number
  /** the person is seen (face for head, eyes for gaze) and the point can be used */
  found: boolean
  /** where the person points on the screen (fractions of the window), null when not seen */
  point: ScreenPoint | null
  /** 0..1 */
  confidence: number
}

/** POINT's `source` for each point source. */
export const POINT_SOURCE: Record<SourceName, PointSource> = { head: 'webcam', gaze: 'gaze' }

/**
 * Which source drives the highlight: Webcam = the head, Gaze = the gaze, Auto = the gaze while it is
 * available, else the head (and the Core scans when neither sees the person). Null = the board does
 * not point in this mode (Scan, Head tilt).
 */
export function chooseSource(mode: PointingMode | null, gazeAvailable: boolean): SourceName | null {
  switch (mode) {
    case 'webcam':
      return 'head'
    case 'gaze':
      return 'gaze'
    case 'auto':
      return gazeAvailable ? 'gaze' : 'head'
    default:
      return null
  }
}

/** A head-tracker frame as a point sample. */
export function fromHead(s: HeadSample, range: HeadRange): PointSample {
  return {
    source: 'head',
    t: s.t,
    found: s.face && s.angles !== null,
    point: s.angles ? poseToPoint(s.angles, range) : null,
    confidence: s.face ? 1 : 0,
  }
}
