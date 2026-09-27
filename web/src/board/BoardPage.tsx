import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { CarState, Confirm, HeadRange, Lang, Message, PointingMode, Screen } from '../contracts'
import DevPanel from '../dev/DevPanel'
import { InputLog } from '../sensor/InputLog'
import { MusePanel } from '../sensor/MusePanel'
import { CalibrationOverlay } from '../facetrack/CalibrationOverlay'
import { loadHeadRange } from '../facetrack/headRange'
import {
  CameraLight,
  CameraNotice,
  CursorDot,
  DwellRing,
  EyesNotice,
  GazeNotice,
  PointerBadge,
} from '../facetrack/indicators'
import { DEFAULT_RANGE } from '../facetrack/pose'
import {
  nativeCarAvailable,
  nativeGazeActive,
  playNativeCarEffect,
  setNativeCarLayout,
  setNativeCarSpeed,
  reportPointingMode,
  showNativeCar,
  subscribeNativeGaze,
} from '../facetrack/native'
import { gazeTuning } from '../facetrack/stores'
import { STICKY_MARGIN } from '../facetrack/tiles'
import { boardPoints, headCamera, usePointing } from '../facetrack/usePointing'
import { StatusDot } from '../lib/StatusDot'
import { useSocket, type Send } from '../lib/useSocket'
import { click, say, unlockSpeech, type Utterance, type VoiceSource } from './speech'
import { STRINGS } from './strings'
import { toastFor, useToasts } from './toast'
import { useCarAnimation } from './carAnimation'
import { TripConfirm, TripSpeaking, TripView } from './trip'
import { tripLayout } from './tripLayout'
import { ToastStack } from './ToastStack'
import { BackPromptView, Breadcrumb, ConfirmView, HelpCountdownView, SpeakingView, StartOverlay, TileGrid } from './views'

type View =
  | { kind: 'waiting' }
  | { kind: 'menu'; screen: Screen }
  | { kind: 'confirm'; confirm: Confirm }
  | { kind: 'speaking'; text: string }
  | { kind: 'help'; countdown: number }

/**
 * Patient board. It is "dumb" (PRD A3.3): it draws what the Core sends and reports back only
 * READY, AUDIO_DONE, RESET (once, when "Click to start" is clicked: Home, first tile) and, in
 * Webcam or Auto mode, POINT and FACE_OK. All decisions, including the
 * highlight, stay in the Core.
 *
 * Speech: only a phrase (the confirmed sentence) shows the speaking screen. Echoes (picked tiles),
 * the "Other..." click and system lines play over whatever is on screen, through one queue (speech.ts). AUDIO_DONE goes back for phrases and system
 * lines, never for echoes.
 *
 * Pointing (facetrack/): the head (camera on only in Webcam or Auto mode, with a "Camera on" light)
 * or an eye tracker plugged into the gaze slot (Gaze or Auto mode, docs/eye-tracking.md). Tracking
 * runs in this page and only POINT / FACE_OK are sent.
 */
export default function BoardPage() {
  const [started, setStarted] = useState(false)
  const [view, setViewState] = useState<View>({ kind: 'waiting' })
  // What is on screen as of the last Core message, updated synchronously (not after a render), so
  // dwell select can never act on a menu the Core has already left (e.g. for the confirm screen).
  const shown = useRef<View>({ kind: 'waiting' })
  const setView = (v: View) => {
    shown.current = v
    setViewState(v)
  }
  const [lang, setLang] = useState<Lang>('en')
  const [mode, setMode] = useState<PointingMode | null>(null)
  const [margin, setMargin] = useState(STICKY_MARGIN) // SETTINGS tile_switch_margin
  const [voiceSource, setVoiceSource] = useState<VoiceSource | null>(null)
  const [range, setRange] = useState<HeadRange | null>(null) // null = not calibrated: defaults
  const [calibrating, setCalibrating] = useState(false)
  // The "Go back?" prompt after a double blink (BACK_PROMPT). `at` restarts the bar for a new prompt.
  const [backPrompt, setBackPrompt] = useState<{ kind: 'menu' | 'confirm'; ms: number; at: number } | null>(null)
  const { toasts, push } = useToasts()
  // Trip mode (SETTINGS trip) and the trip control animation in progress (CAR_ACTION).
  const [trip, setTrip] = useState(false)
  const car = useCarAnimation()
  const [carState, setCarState] = useState<CarState | null>(null) // CAR_STATE: the trip telemetry
  const nativeCar = nativeCarAvailable()
  // "Click to start" sends RESET once (Home, first tile); a later reconnect only sends READY.
  const resetPending = useRef(false)

  useEffect(() => {
    if (!backPrompt) return
    const timer = window.setTimeout(() => setBackPrompt(null), backPrompt.ms + 1000)
    return () => window.clearTimeout(timer)
  }, [backPrompt])

  const onMessage = (msg: Message, send: Send) => {
    switch (msg.type) {
      case 'SCREEN':
        setLang(msg.lang)
        if (msg.screen === 'help_countdown') setView({ kind: 'help', countdown: msg.countdown ?? 0 })
        else setView({ kind: 'menu', screen: msg })
        break
      case 'CONFIRM':
        setView({ kind: 'confirm', confirm: msg })
        break
      case 'BACK_PROMPT':
        setBackPrompt(msg.open ? { kind: msg.kind, ms: msg.timeout_ms, at: Date.now() } : null)
        break
      case 'SPEAK':
      case 'PLAY_AUDIO': {
        const u: Utterance = {
          id: msg.id,
          kind: msg.kind,
          text: msg.text,
          lang: msg.lang,
          audio: msg.type === 'PLAY_AUDIO' ? { url: msg.url, cached: msg.cached } : undefined,
        }
        if (msg.kind === 'phrase') {
          setLang(msg.lang)
          setView({ kind: 'speaking', text: msg.text })
        }
        const onEnd = (ended: Utterance) => {
          if (ended.kind !== 'echo') send({ type: 'AUDIO_DONE', id: ended.id })
        }
        say(u, onEnd, setVoiceSource)
        break
      }
      case 'CLICK':
        click() // a picked "Other...": no word, a soft click in the sound queue
        break
      case 'CAR_ACTION':
        // The Core has locked input for `ms`; the tiles and the tablet's car play the sequence.
        car.start(msg.action, msg.ms, msg.window ?? null)
        playNativeCarEffect(msg.action, msg.ms, msg.window ?? null)
        break
      case 'CAR_STATE':
        setCarState(msg)
        setNativeCarSpeed(msg.speed_mph)
        break
      case 'SIGNAL':
        break // MusePanel displays sensor telemetry through its console socket.
      case 'ACTION_RESULT':
        push(toastFor(msg, lang))
        break
      case 'SETTINGS':
        // The language and the pointing mode (camera on or off); the dev panel shows the rest.
        if (msg.lang) setLang(msg.lang)
        // The tablet shell first: in a camera mode it claims the camera before this render decides
        // whether the page opens it (native.ts). SETTINGS only arrive after "Click to start".
        reportPointingMode(msg.pointing_mode ?? 'off')
        setMode(msg.pointing_mode)
        if (msg.tile_switch_margin !== undefined) setMargin(msg.tile_switch_margin)
        if (msg.trip != null) setTrip(msg.trip)
        break
      default:
        console.warn('board ignored', msg.type)
    }
  }

  // The board goes live only after the click that unlocks speech, so no SPEAK can arrive muted.
  const { status, send } = useSocket('/ws/board', {
    enabled: started,
    onOpen: (send) => {
      if (resetPending.current && send({ type: 'RESET' })) resetPending.current = false
      send({ type: 'READY' })
    },
    onMessage,
  })
  const connected = status === 'open'

  // The saved head range, fetched again whenever the Core (re)connects.
  useEffect(() => {
    if (!connected) return
    let live = true
    loadHeadRange().then(
      (saved) => live && setRange(saved),
      (e) => console.warn('head range not loaded, using the defaults', e),
    )
    return () => {
      live = false
    }
  }, [connected])

  // Re-render when the tablet shell's tracker starts or stops: it decides who owns the camera.
  useSyncExternalStore(subscribeNativeGaze, nativeGazeActive)
  const camera = started && headCamera(mode)
  const pointing = started && boardPoints(mode)
  const screen = connected && view.kind === 'menu' ? view.screen : null
  // Touch: a tap on a tile picks it, a tap on the "Say this?" sentence confirms (TAP; the Core checks
  // the screen is still the one tapped). For a caregiver, or testing without a headband.
  const tapTile = (tile: number) => {
    if (screen) send({ type: 'TAP', tile, seq: screen.seq, t: Date.now() / 1000 })
  }
  const tapConfirm = () => send({ type: 'TAP', tile: null, seq: null, t: Date.now() / 1000 })
  const tapCancel = () => send({ type: 'TAP', tile: null, seq: null, cancel: true, t: Date.now() / 1000 })
  const tripScreen = screen?.screen === 'trip' ? screen : null
  // The trip layout is on while trip mode is (the tablet's car shows behind it, the page see-through).
  const tripShown = started && connected && (tripScreen !== null || (trip && view.kind !== 'help'))
  const seeThrough = tripShown && nativeCar

  // Dwell select (off by default): a long look at a menu tile sends CLENCH on /ws/input, the same
  // event the headband sends. usePointing only calls pick() on a menu screen, never on the confirm
  // screen or the help countdown.
  const dwellOn = useSyncExternalStore(gazeTuning.subscribe, gazeTuning.get).dwell
  const input = useSocket('/ws/input', { enabled: started && dwellOn })
  const pick = (seq: number) => {
    const v = shown.current
    if (v.kind !== 'menu' || v.screen.seq !== seq || v.screen.loading) return false
    return input.send({ type: 'CLENCH', t: Date.now() / 1000, strength: 1.0 })
  }
  usePointing({ mode, started, connected, screen, send, range: range ?? DEFAULT_RANGE, paused: calibrating, margin, pick })

  const start = () => {
    unlockSpeech()
    resetPending.current = true
    setStarted(true)
  }

  // The tablet shell draws the car behind the page while the trip screen shows, framed for the
  // layout (car / split / map).
  const layout = useSyncExternalStore(tripLayout.subscribe, tripLayout.get)
  useEffect(() => {
    showNativeCar(tripShown)
  }, [tripShown])
  useEffect(() => {
    setNativeCarLayout(layout)
  }, [layout])
  useEffect(() => () => showNativeCar(false), [])

  // See-through only where the shell draws behind (index.css paints the page black otherwise).
  useEffect(() => {
    const bg = seeThrough ? 'transparent' : ''
    document.documentElement.style.background = bg
    document.body.style.background = bg
  }, [seeThrough])

  return (
    <div className={`flex h-screen flex-col overflow-hidden text-white ${seeThrough ? 'bg-transparent' : 'bg-black'}`}>
      {!started && <StartOverlay onStart={start} />}
      <div className="fixed right-4 top-4 z-30 flex flex-col items-end gap-2">
        <div className="flex items-center gap-3">
          {pointing && <EyesNotice lang={lang} />}
          {screen && <PointerBadge screen={screen} mode={mode} />}
          <CameraLight lang={lang} />
          <StatusDot status={started ? status : 'closed'} label="Core" />
        </div>
        {started && <CameraNotice lang={lang} mode={mode} />}
        {started && <GazeNotice lang={lang} mode={mode} />}
      </div>

      {/* Never show a stale highlight while disconnected: the Core may have moved on. */}
      {(!connected || view.kind === 'waiting') && (
        <div className="flex flex-1 items-center justify-center text-4xl text-zinc-400">
          {STRINGS[lang].connecting}
        </div>
      )}
      {tripScreen && (
        <>
          <TripView
            screen={tripScreen}
            car={carState}
            anim={car.anim}
            phase={car.phase}
            tint={car.tint}
            nativeCar={nativeCar}
            onTap={tapTile}
          />
          {pointing && <CursorDot />}
          {pointing && <DwellRing />}
        </>
      )}
      {screen && !tripScreen && (
        <>
          <Breadcrumb screen={screen} />
          <TileGrid screen={screen} onTap={tapTile} />
          {pointing && <CursorDot />}
          {pointing && <DwellRing />}
        </>
      )}
      {connected && view.kind === 'confirm' && (view.confirm.action === 'pull_over' || view.confirm.action === 'support') && (
        <TripConfirm
          action={view.confirm.action}
          lang={lang}
          car={carState}
          nativeCar={nativeCar}
          onConfirm={tapConfirm}
          onCancel={tapCancel}
        />
      )}
      {connected && view.kind === 'confirm' && view.confirm.action !== 'pull_over' && view.confirm.action !== 'support' && (
        <ConfirmView confirm={view.confirm} lang={lang} onTap={tapConfirm} />
      )}
      {connected && view.kind === 'speaking' && trip && (
        <TripSpeaking lang={lang} text={view.text} car={carState} nativeCar={nativeCar} tint={car.tint} />
      )}
      {connected && view.kind === 'speaking' && !trip && <SpeakingView text={view.text} lang={lang} />}
      {connected && view.kind === 'help' && <HelpCountdownView countdown={view.countdown} lang={lang} />}
      {connected && backPrompt && view.kind !== 'help' && (
        <BackPromptView key={backPrompt.at} kind={backPrompt.kind} ms={backPrompt.ms} lang={lang} />
      )}
      <ToastStack toasts={toasts} />
      <MusePanel />
      <InputLog />

      {calibrating && (
        <CalibrationOverlay lang={lang} onSaved={setRange} onClose={() => setCalibrating(false)} />
      )}
      <DevPanel
        voiceSource={voiceSource}
        headRange={range}
        cameraWanted={camera}
        onCalibrate={() => setCalibrating(true)}
      />
    </div>
  )
}
