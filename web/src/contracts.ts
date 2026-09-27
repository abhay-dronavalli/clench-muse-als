// Shared event formats (PRD A4). Single source of truth, together with core/contracts.py.
//
// Any change here must update core/contracts.py and docs/contracts.md in the same commit.
//
// All messages are small JSON objects sent over WebSocket. Every message has a `type`
// field that names it. Timestamps `t` are float seconds since the Unix epoch.
// Tile indexes (`tile`, `highlight`) are 0-based positions in the current SCREEN's `tiles`.

export const POINTING_MODES = ['auto', 'scan', 'webcam', 'gaze', 'headtilt'] as const
export type PointingMode = (typeof POINTING_MODES)[number]
/**
 * webcam = the board's head pose; gaze = an eye tracker plugged into the board
 * (docs/eye-tracking.md); headtilt = the headband's motion sensor (Sensor Service).
 */
export type PointSource = 'webcam' | 'gaze' | 'headtilt'
/**
 * Where the highlight is coming from right now: Auto shows 'scan' after falling back (and 'gaze' or
 * 'webcam' while following), and Head tilt shows 'scan' until it is built.
 */
export type ActivePointer = 'scan' | 'webcam' | 'gaze' | 'headtilt'
export type BodyStateLevel = 'calm' | 'normal' | 'elevated'
export type Lang = 'en' | 'es'
export type ScreenName = 'menu' | 'suggestions' | 'help_countdown' | 'paused' | 'calibrating' | 'computer'
export type ActionName = 'speak' | 'send_message' | 'place_call' | 'room_control' | 'help_alert'
/**
 * phrase = a confirmed sentence (the Core waits for its AUDIO_DONE); echo = a picked tile's label
 * said as it is picked; system = a fixed line from the Core (help alert). Only phrases change state.
 */
export type UtteranceKind = 'phrase' | 'echo' | 'system'
/**
 * branch = opens a smaller menu; leaf = an option that leads to a sentence (menu or AI-made);
 * suggestion = a full sentence, picking it opens the confirm screen; other = "Other..." (the next page of new options).
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

/** Clench held `long_clench_ms` (2.5 s by default): start the help alert countdown. */
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
  connected?: boolean | null
  profile?: string | null
  emg?: number | null
  threshold?: number | null
  blocked?: string | null
}

// --- Board -> Core (Webcam mode) / Sensor Service -> Core (Head tilt mode) ---

/**
 * The person is facing tile `tile` of the SCREEN numbered `seq`. Sent when the tile changes and once
 * for every new SCREEN. The Core ignores a POINT whose `seq` is not the current screen's.
 */
export interface Point {
  type: 'POINT'
  source: PointSource
  /** 0-based, >= 0 */
  tile: number
  /** the SCREEN `seq` the tile index belongs to; integer >= 0 */
  seq: number
  t: number
}

/**
 * Can the board see the person (the head for webcam pointing, the eyes for gaze)? Auto mode falls
 * back to Scan when false for ~3 s.
 */
export interface FaceOk {
  type: 'FACE_OK'
  ok: boolean
}

export interface ComputerTile {
  id: string
  label: string
  left: number
  top: number
  right: number
  bottom: number
}

export interface ComputerState {
  type: 'COMPUTER_STATE'
  active: boolean
  seq: number
  tiles: ComputerTile[]
  highlight: number | null
  paused: boolean
  pointer: ActivePointer
}

export interface ComputerPoint {
  type: 'COMPUTER_POINT'
  seq: number
  tile: number | null
  source: 'webcam' | 'gaze'
  found: boolean
  status: 'tracking' | 'no_tracker' | 'lost' | 'camera_error' | 'starting' | 'off'
  t: number
  pick?: boolean
}

export interface ComputerTelemetry {
  type: 'COMPUTER_TELEMETRY'
  x: number | null
  y: number | null
  show_cursor: boolean
  dwell: boolean
  progress: number
  camera: 'on' | 'off' | 'starting' | 'error'
  yaw: number | null
  pitch: number | null
  eye_connected: boolean
  head_range: HeadRange | null
  voice_source: string | null
}

export interface ComputerControl {
  type: 'COMPUTER_CONTROL'
  action: 'cursor' | 'dwell' | 'retry' | 'calibrate' | 'calibration_done' | 'head_range'
  value?: boolean
  head_range?: HeadRange
}

// --- Board -> Core ---

/** The board connected and is ready to draw. The Core replies with the current view. */
export interface Ready {
  type: 'READY'
}

/**
 * Back to Home: clears the screen stack and puts the highlight on the first tile (Suggested). Sent by
 * the board when "Click to start" is clicked and by the dev panel's "Reset to Home".
 */
export interface Reset {
  type: 'RESET'
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
  /** rank by the patient's history; false = "Day 1 mode" (menu.yaml order); omit to keep it */
  learning?: boolean
  /** integer ms a clench must be held to count as a LONG_CLENCH (help), 1000 to 5000; omit to keep it */
  long_clench_ms?: number
  /**
   * webcam / gaze pointing: how far (share of a tile's size) the point must be inside a new tile
   * before the highlight moves there, 0 to 0.2; omit to keep it
   */
  tile_switch_margin?: number
  /** Session-only permission for the separate Muse input; defaults to paused. */
  muse_enabled?: boolean
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
  /** goes up every time the tiles change (not when only the highlight moves); POINT echoes it */
  seq: number
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
  /**
   * where the highlight comes from right now (null on the help countdown); 'scan' in Auto or Head
   * tilt mode means the fallback is on: the board shows a small "Scanning" badge
   */
  pointer?: ActivePointer | null
}

/** The "Send this?" screen. Nothing is spoken or sent without a confirming clench (PRD D5). */
export interface Confirm {
  type: 'CONFIRM'
  text: string
  action: ActionName
}

/**
 * A DOUBLE_BLINK asked to go back (a menu) or to cancel the "Say this?" screen. It only happens if a
 * CLENCH follows within `timeout_ms`; doing nothing closes the prompt and nothing changes. Sent again
 * with `open` false when the prompt closes, for any reason.
 */
export interface BackPrompt {
  type: 'BACK_PROMPT'
  open: boolean
  /** menu = up one level; confirm = cancel the "Say this?" screen */
  kind: 'menu' | 'confirm'
  /** how long it stays open; 0 when closing */
  timeout_ms: number
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

/**
 * Play the short soft click (a picked "Other..."; no word is said for it). It goes into the board's
 * sound queue in order with the echoes. Only sent when speak picks is on.
 */
export interface Click {
  type: 'CLICK'
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

// --- Board <-> Core over REST (not a WebSocket message): GET / PUT /api/head-range ---

/**
 * The person's comfortable head range from the calibration overlay, in degrees of head yaw and pitch
 * as the board measures them. Saved in the database profile (PRD A7 `head_range_json`). Left and
 * right lie on opposite sides of the center, as do up and down, each at least 2 degrees away.
 */
export interface HeadRange {
  center_yaw: number
  center_pitch: number
  left_yaw: number
  right_yaw: number
  up_pitch: number
  down_pitch: number
}

// --- Core -> Console and web dev panel ---

/**
 * What one confirmed message cost (PRD section 12, D7), and what it would have cost in Day 1 mode
 * (menu.yaml order, no shortcut). Sent after every confirm, to consoles and input clients.
 */
export interface Metrics {
  type: 'METRICS'
  /** the confirmed sentence */
  text: string
  /** clenches since home, the confirm clench included; >= 1 */
  selections: number
  /** highlight moves waited through before the picks; >= 0 */
  scan_steps: number
  day1_selections: number
  day1_scan_steps: number
}

export type InputSource = 'muse' | 'dev'

/**
 * One raw input gesture and what the Core did with it. Sent to consoles and input clients only,
 * for the board's input log (press "/"): a caregiver checking whether the headband is picking up a
 * clench, a long clench or a double blink needs to see the gestures the Core REFUSED too, and why.
 */
export interface InputEvent {
  type: 'INPUT_EVENT'
  t: number
  kind: 'CLENCH' | 'LONG_CLENCH' | 'DOUBLE_BLINK'
  /** muse = headband sensor service, dev = keyboard stand-in */
  source: InputSource
  /** false = the Core ignored it (paused, stale, blocked, or no patient board) */
  accepted: boolean
  /** why it was ignored; null when accepted */
  reason: string | null
  /** CLENCH only, 0..1 */
  strength: number | null
  /** LONG_CLENCH only, seconds */
  duration: number | null
}

export type JevStatus = 'off' | 'waiting' | 'answered'

/**
 * Why the one-clench Suggested shortcut is on or off right now. Sent after every Home render (and
 * again when Jev's answer arrives while Home shows), to consoles and input clients only.
 */
export interface ShortcutDebug {
  type: 'SHORTCUT_DEBUG'
  /** the history's top Suggested phrase; null in Day 1 mode or with no phrases */
  top: string | null
  /** its share of what was said around this hour, 0..1 */
  history_share: number
  /** off = not configured, paused or Day 1 mode; waiting = no answer yet */
  jev: JevStatus
  /** the phrase Jev picked (null unless jev is 'answered') */
  jev_pick: string | null
  /** Jev's confidence in its pick, 0..1 */
  jev_confidence: number | null
  /** picking Suggested goes straight to the confirm screen with `top` */
  shortcut: boolean
  /** e.g. "history share 0.72 >= 0.6" */
  reason: string
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
  | ComputerState
  | ComputerPoint
  | ComputerTelemetry
  | ComputerControl
  | Ready
  | Reset
  | AudioDone
  | Settings
  | Screen
  | Confirm
  | BackPrompt
  | Speak
  | PlayAudio
  | Click
  | ActionResult
  | Metrics
  | ShortcutDebug
  | InputEvent

export type MessageType = Message['type']
