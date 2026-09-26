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
export type ActionName = 'speak' | 'send_text' | 'place_call' | 'room_control' | 'help_alert'

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

// --- Console -> Core ---

/** Caregiver settings change. */
export interface Settings {
  type: 'SETTINGS'
  pointing_mode: PointingMode
  /** integer ms per tile in Scan mode, > 0 */
  scan_ms: number
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
}

/** The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5). */
export interface Confirm {
  type: 'CONFIRM'
  text: string
  action: ActionName
}

/** Play an audio file served by the Core. */
export interface PlayAudio {
  type: 'PLAY_AUDIO'
  url: string
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
  | Settings
  | Screen
  | Confirm
  | PlayAudio

export type MessageType = Message['type']
