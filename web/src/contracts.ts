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
/**
 * phrase = a confirmed sentence (the Core waits for its AUDIO_DONE); echo = a picked tile's label
 * said as it is picked; system = a fixed line from the Core (help alert). Only phrases change state.
 */
export type UtteranceKind = 'phrase' | 'echo' | 'system'
/**
 * branch = opens a smaller menu; leaf = an option that leads to a sentence (menu or AI-made);
 * suggestion = a full sentence, picking it opens the confirm screen; other = "Other..." / "Spell it".
 */
export type TileKind = 'branch' | 'leaf' | 'suggestion' | 'other'

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

/** Speech (SPEAK) or audio (PLAY_AUDIO) `id` finished, failed or was interrupted on the board. */
export interface AudioDone {
  type: 'AUDIO_DONE'
  /** the SPEAK / PLAY_AUDIO id */
  id: string
}

// --- Console -> Core (the web dev panel also sends it), and Core -> every client ---

/**
 * Caregiver settings change. The Core also sends it, fully filled in, to every client on connect
 * and after every change.
 */
export interface Settings {
  type: 'SETTINGS'
  pointing_mode: PointingMode
  /** integer ms per tile in Scan mode, > 0 */
  scan_ms: number
  /** omit to keep the current language */
  lang?: Lang
  /** say each picked tile aloud; omit to keep the current value */
  speak_picks?: boolean
}

// --- Core -> Board ---

export interface Tile {
  /** dotted menu path; "ai:..." for AI-made options and sentences */
  id: string
  label: string
  kind: TileKind
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
  /** true while the Core waits (at most 4 s) for AI options after a pick; scanning is paused */
  loading?: boolean
}

/** The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5). */
export interface Confirm {
  type: 'CONFIRM'
  text: string
  action: ActionName
}

/**
 * Say `text` with the browser's speech synthesis (no cloud audio for it right now).
 * A phrase is only ever sent after a confirming clench (PRD D5).
 */
export interface Speak {
  type: 'SPEAK'
  /** utterance id, echoed back in AUDIO_DONE */
  id: string
  kind: UtteranceKind
  text: string
  lang: Lang
}

/** Play cloud TTS audio served by the Core (/audio/<hash>.mp3). Same rules as SPEAK. */
export interface PlayAudio {
  type: 'PLAY_AUDIO'
  id: string
  kind: UtteranceKind
  url: string
  /** what the audio says; the board speaks it with browser speech if the file fails */
  text: string
  lang: Lang
  /** true = the file was already on disk (no request to the TTS service) */
  cached: boolean
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
