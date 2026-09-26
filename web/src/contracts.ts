// Shared event formats (PRD A4). Single source of truth, together with core/contracts.py.
//
// Any change here must update core/contracts.py and docs/contracts.md in the same commit.
//
// All messages are small JSON objects sent over WebSocket. Every message has a `type`
// field that names it. Timestamps `t` are float seconds since the Unix epoch.
// Tile indexes (`tile`, `highlight`) are 0-based positions in the current SCREEN's `tiles`.

export const POINTING_MODES = ['auto', 'scan', 'webcam', 'headtilt'] as const
export type PointingMode = (typeof POINTING_MODES)[number]
export type PointSource = 'webcam' | 'headtilt'
export type BodyStateLevel = 'calm' | 'normal' | 'elevated'
export type Lang = 'en' | 'es'
export type ScreenName = 'menu' | 'suggestions' | 'help_countdown' | 'paused' | 'calibrating'
export type ActionName = 'speak' | 'send_message' | 'place_call' | 'room_control' | 'help_alert'

// --- Sensor Service -> Core (the web dev panel also sends the first three) ---

/** Short jaw clench: pick the highlighted tile. */
export interface Clench {
  type: 'CLENCH'
  t: number
  /** 0 to 1 */
  strength: number
}

/** Two blinks close together: go back / no / cancel. */
export interface DoubleBlink {
  type: 'DOUBLE_BLINK'
  t: number
}

/** Clench held about 1.5 s: start the help alert countdown. */
export interface LongClench {
  type: 'LONG_CLENCH'
  t: number
  /** seconds held, > 0 */
  duration: number
}

/** Body state. Only reorders options, never takes action (PRD D10). */
export interface State {
  type: 'STATE'
  t: number
  level: BodyStateLevel
  /** heart rate in bpm; null when PPG has no reading */
  hr: number | null
  /** restlessness, 0 = still */
  motion: number
  eyes_closed: boolean
}

/** Thinned signal sample for the caregiver chart only. One value per channel. */
export interface Signal {
  type: 'SIGNAL'
  t: number
  ch: number[]
}

// --- Board -> Core (Webcam mode) / Sensor Service -> Core (Head tilt mode) ---

/** The person is facing tile `tile`. Sent only when the tile changes. */
export interface Point {
  type: 'POINT'
  source: PointSource
  /** 0-based, >= 0 */
  tile: number
  t: number
}

/** Webcam face tracking status. Auto mode falls back to Scan when false for ~3 s. */
export interface FaceOk {
  type: 'FACE_OK'
  ok: boolean
}

// --- Board -> Core ---

/** The board connected and is ready to draw. The Core replies with the current view. */
export interface Ready {
  type: 'READY'
}

/** Speech (SPEAK) or audio (PLAY_AUDIO) finished or failed on the board. */
export interface AudioDone {
  type: 'AUDIO_DONE'
}

// --- Console -> Core (the web dev panel also sends it) ---

/** Caregiver settings change. */
export interface Settings {
  type: 'SETTINGS'
  pointing_mode: PointingMode
  /** integer ms per tile in Scan mode, > 0 */
  scan_ms: number
  /** omit to keep the current language */
  lang?: Lang
}

// --- Core -> Board ---

export interface Tile {
  id: string
  label: string
}

/** What the board should draw. The Core owns the highlight; the board only draws it. */
export interface Screen {
  type: 'SCREEN'
  screen: ScreenName
  /** at most 6 (PRD D8) */
  tiles: Tile[]
  /** 0-based; null = nothing highlighted */
  highlight: number | null
  lang: Lang
  /** breadcrumb labels from home down to this level; [] at home */
  path: string[]
  /** seconds left before the help alert fires; set only when screen is 'help_countdown' (tiles = []) */
  countdown?: number | null
}

/** The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5). */
export interface Confirm {
  type: 'CONFIRM'
  text: string
  action: ActionName
}

/** Speak `text` with the browser's speech synthesis. Only sent after a confirming clench. */
export interface Speak {
  type: 'SPEAK'
  text: string
  lang: Lang
}

/** Play an audio file served by the Core (later, for cloud voices). */
export interface PlayAudio {
  type: 'PLAY_AUDIO'
  url: string
}

/** How a confirmed action that leaves the laptop went (message, call, room control). */
export interface ActionResult {
  type: 'ACTION_RESULT'
  action: ActionName
  ok: boolean
  /** "dry run" when ACTIONS_DRY_RUN is on; the service's error message on failure */
  detail: string
  /** contact's display name in the current language; null when there is none */
  contact: string | null
}

// --- Union ---

export type Message =
  | Clench
  | DoubleBlink
  | LongClench
  | State
  | Signal
  | Point
  | FaceOk
  | Ready
  | AudioDone
  | Settings
  | Screen
  | Confirm
  | Speak
  | PlayAudio
  | ActionResult

export type MessageType = Message['type']
