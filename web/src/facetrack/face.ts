// FACE_OK debounce: the board reports "face seen" / "face lost" only once it has held for a short
// while, so one missed frame (a blink, a hand) does not flip Auto mode. Pure, unit tested.

export const FACE_DEBOUNCE_MS = 300

export class FaceDebouncer {
  /** What was last reported; null before the first report. */
  reported: boolean | null = null
  private candidate: boolean | null = null
  private since = 0
  private readonly delayMs: number

  constructor(delayMs = FACE_DEBOUNCE_MS) {
    this.delayMs = delayMs
  }

  /**
   * One frame: was a face seen at time `now` (ms)? Returns the new state to report when it has held
   * for the debounce time and differs from the last report; otherwise null.
   */
  update(seen: boolean, now: number): boolean | null {
    if (seen !== this.candidate) {
      this.candidate = seen
      this.since = now
    }
    if (seen !== this.reported && now - this.since >= this.delayMs) {
      this.reported = seen
      return seen
    }
    return null
  }

  /** Forget everything (camera stopped); the next steady state is reported again. */
  reset(): void {
    this.reported = null
    this.candidate = null
  }
}
