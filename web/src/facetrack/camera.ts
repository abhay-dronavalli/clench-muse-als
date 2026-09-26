// Camera problems in plain terms, so the board can tell the caregiver what to do. Pure, unit tested.

export type CameraProblem = 'denied' | 'none' | 'busy' | 'insecure' | 'model' | 'error'

/**
 * What a getUserMedia failure means at the bedside: permission denied (the browser or Windows camera
 * privacy setting), no camera, or the camera in use by another app (Teams, Zoom, the Camera app).
 */
export function cameraProblem(e: unknown): CameraProblem {
  const name = e instanceof DOMException || e instanceof Error ? e.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError' || name === 'PermissionDeniedError') return 'denied'
  if (name === 'NotFoundError' || name === 'OverconstrainedError' || name === 'DevicesNotFoundError') return 'none'
  if (name === 'NotReadableError' || name === 'TrackStartError' || name === 'AbortError') return 'busy'
  return 'error'
}
