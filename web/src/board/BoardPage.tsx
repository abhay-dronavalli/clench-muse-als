import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ControlRow, MediaPlayer, type NowPlaying } from './MediaPlayer'
import type { ActivePointer, CarState, ComputerState, Confirm, HeadRange, Lang, Message, PointingMode, Screen, Settings, Tile, TripLayout } from '../contracts'
import { useComputerPointing } from '../facetrack/useComputerPointing'
import DevPanel from '../dev/DevPanel'
import { InputLog } from '../sensor/InputLog'
import { MusePanel } from '../sensor/MusePanel'
import { CalibrationOverlay } from '../facetrack/CalibrationOverlay'
import { EyeCalibrationOverlay } from '../facetrack/EyeCalibrationOverlay'
import { EyeSetupOverlay } from '../facetrack/EyeSetupOverlay'
import { loadHeadRange, saveHeadRange } from '../facetrack/headRange'
import { tracker } from '../facetrack/tracker'
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
import { gazeOwnsCamera, subscribeGazeOwner } from '../facetrack/cameraOwner'
import { eyedidWeb } from '../facetrack/eyedidWeb'
import {
  nativeBridge,
  nativeCarAvailable,
  playNativeCarEffect,
  setNativeCarLayout,
  setNativeCarSpeed,
  reportPointingMode,
  showNativeCar,
} from '../facetrack/native'
import { gazeTuning, showCursor } from '../facetrack/stores'
import { STICKY_MARGIN } from '../facetrack/tiles'
import { boardPoints, headCamera, usePointing } from '../facetrack/usePointing'
import { StatusDot } from '../lib/StatusDot'
import { useSocket, type Send } from '../lib/useSocket'
import { click, say, unlockSpeech, type Utterance, type VoiceSource } from './speech'
import { STRINGS } from './strings'
import { toastFor, useToasts } from './toast'
import { useCarAnimation } from './carAnimation'
import { CarOnboarding } from './CarOnboarding'
import { Onboarding } from './Onboarding'
import { effectiveTripLayout } from './onboardingFlow'
import { TripConfirm, TripSpeaking, TripView } from './trip'
import { ToastStack } from './ToastStack'
import { BackPromptView, Breadcrumb, ConfirmView, HelpCountdownView, SpeakingView, StartOverlay, TileGrid } from './views'

type View =
  | { kind: 'waiting' }
  | { kind: 'menu'; screen: Screen }
  | { kind: 'confirm'; confirm: Confirm }
  | { kind: 'speaking'; text: string }
  | { kind: 'help'; countdown: number }
  | { kind: 'computer' }

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
  const [computer, setComputer] = useState<ComputerState | null>(null)
  const computerShown = useRef<ComputerState | null>(null)
  const [onboarding, setOnboarding] = useState(false)
  const onboardingRef = useRef(false)
  const [tripPointer, setTripPointer] = useState<ActivePointer | null>(null)
  const [view, setViewState] = useState<View>({ kind: 'waiting' })
  // What is on screen as of the last Core message, updated synchronously (not after a render), so
  // dwell select can never act on a menu the Core has already left (e.g. for the confirm screen).
  const shown = useRef<View>({ kind: 'waiting' })
  const setView = (v: View) => {
    shown.current = v
    setViewState(v)
  }
  const [lang, setLang] = useState<Lang>('en')
  // Computer > YouTube / Spotify: what plays on the board, and the last control command for it.
  const [media, setMedia] = useState<NowPlaying | null>(null)
  const [mediaCommand, setMediaCommand] = useState<{ action: 'pause' | 'resume' | 'restart' | 'volume_down' | 'volume_up'; n: number } | null>(null)
  const [mode, setMode] = useState<PointingMode | null>(null)
  const [margin, setMargin] = useState(STICKY_MARGIN) // SETTINGS tile_switch_margin
  const [voiceSource, setVoiceSource] = useState<VoiceSource | null>(null)
  const [range, setRange] = useState<HeadRange | null>(null) // null = not calibrated: defaults
  const [calibrating, setCalibrating] = useState(false)
  const [computerCalibrating, setComputerCalibrating] = useState(false)
  const [eyeCalibrating, setEyeCalibrating] = useState(false) // Eyedid web's five dots
  // The "Go back?" prompt after a double blink (BACK_PROMPT). `at` restarts the bar for a new prompt.
  const [backPrompt, setBackPrompt] = useState<{ kind: 'menu' | 'confirm'; ms: number; at: number } | null>(null)
  const { toasts, push } = useToasts()
  // Trip mode (SETTINGS trip) and the trip control animation in progress (CAR_ACTION).
  const [trip, setTrip] = useState(false)
  // Car mode's short onboarding (CarOnboarding): runs each time Car mode opens.
  const [carSetup, setCarSetup] = useState(false)
  const tripRef = useRef(false)
  const car = useCarAnimation()
  const [carState, setCarState] = useState<CarState | null>(null) // CAR_STATE: the trip telemetry
  const [layout, setLayout] = useState<TripLayout>('car') // SETTINGS trip_layout
  const lastSettings = useRef<Settings | null>(null) // the layout switch sends it back with the new layout
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
      case 'COMPUTER_STATE':
        computerShown.current = msg
        setComputer(msg)
        if (!msg.active) setComputerCalibrating(false)
        break
      case 'COMPUTER_CONTROL':
        if (msg.action === 'cursor') showCursor.set(!!msg.value)
        if (msg.action === 'dwell') gazeTuning.set({ ...gazeTuning.get(), dwell: !!msg.value })
        if (msg.action === 'retry') void tracker.start()
        if (msg.action === 'calibrate') setComputerCalibrating(true)
        if (msg.action === 'calibration_done') setComputerCalibrating(false)
        if (msg.action === 'head_range' && msg.head_range) void saveHeadRange(msg.head_range).then(setRange).catch(console.error)
        break
      case 'SCREEN':
        if (msg.screen === 'trip') setTripPointer(msg.pointer ?? null)
        setLang(msg.lang)
        if (msg.screen === 'help_countdown') setView({ kind: 'help', countdown: msg.countdown ?? 0 })
        else if (msg.screen === 'computer') setView({ kind: 'computer' })
        else setView({ kind: 'menu', screen: msg })
        break
      case 'CONFIRM':
        setView({ kind: 'confirm', confirm: msg })
        break
      case 'MEDIA':
        if (msg.action === 'play' && msg.provider && msg.id) {
          setMedia({ provider: msg.provider, id: msg.id, title: msg.title ?? '' })
          setMediaCommand(null)
        } else if (msg.action === 'stop') setMedia(null)
        else if (msg.action !== 'play') setMediaCommand((c) => ({ action: msg.action as 'pause', n: (c?.n ?? 0) + 1 }))
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
      case 'CAR_RESULT':
        // The car's answer (core/car). The Core says it when it matters; the toast shows every one.
        push({ tone: msg.status === 'REJECTED' ? 'error' : msg.status === 'DELAYED' ? 'demo' : 'ok', text: msg.message })
        break
      case 'SETTINGS':
        if (onboardingRef.current && (msg.onboarding !== true || msg.pointing_mode !== 'auto')) {
          send({ ...msg, onboarding: true, muse_enabled: false, pointing_mode: 'auto' })
        }
        // The language and the pointing mode (camera on or off); the dev panel shows the rest.
        if (msg.lang) setLang(msg.lang)
        // The tablet shell first: in a camera mode it claims the camera before this render decides
        // whether the page opens it (native.ts). SETTINGS only arrive after "Click to start".
        reportPointingMode(msg.pointing_mode ?? 'off')
        // Eyedid web on a laptop, the same way: a gaze mode claims the webcam before this render.
        eyedidWeb.setMode(msg.pointing_mode ?? 'off')
        setMode(msg.pointing_mode)
        if (msg.tile_switch_margin !== undefined) setMargin(msg.tile_switch_margin)
        if (msg.trip != null) {
          if (msg.trip && !tripRef.current && !onboardingRef.current) setCarSetup(true) // Car mode just opened
          if (!msg.trip) setCarSetup(false)
          tripRef.current = msg.trip
          setTrip(msg.trip)
        }
        if (msg.trip_layout != null) setLayout(msg.trip_layout)
        lastSettings.current = msg
        break
      default:
        console.warn('board ignored', msg.type)
    }
  }

  // The board goes live only after the click that unlocks speech, so no SPEAK can arrive muted.
  const { status, send } = useSocket('/ws/board', {
    enabled: started,
    onOpen: (send) => {
      if (onboardingRef.current) send({ type: 'SETTINGS', pointing_mode: 'auto', scan_ms: lastSettings.current?.scan_ms ?? 1000, onboarding: true, muse_enabled: false })
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

  // Re-render when an eye tracker (tablet shell or Eyedid web) starts or stops: it owns the camera.
  useSyncExternalStore(subscribeGazeOwner, gazeOwnsCamera)
  // Leaving the board gives the webcam back.
  useEffect(() => () => eyedidWeb.setMode('off'), [])
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
  // The trip layout switch: the Core owns the layout (it changes which tiles there are).
  const changeLayout = (next: TripLayout) => {
    const cur = lastSettings.current
    if (cur) send({ type: 'SETTINGS', pointing_mode: cur.pointing_mode, scan_ms: cur.scan_ms, trip_layout: next })
  }
  const displayLayout = effectiveTripLayout(layout, tripPointer)
  const tripShared = { car: carState, nativeCar, layout: displayLayout, onLayout: changeLayout }
  // A Support question (core/car) is drawn on the trip screen too: its answers are big tiles.
  const tripScreen = screen?.screen === 'trip' || screen?.screen === 'support_question' ? screen : null
  // The trip layout is on while trip mode is (the tablet's car shows behind it, the page see-through).
  const tripShown = started && connected && (tripScreen !== null || (trip && view.kind !== 'help'))
  // The car only in Car mode (and its onboarding, where it revolves behind), never in the app onboarding.
  const seeThrough = (tripShown || carSetup) && nativeCar

  // Dwell select (off by default): a long look at a menu tile sends CLENCH on /ws/input, the same
  // event the headband sends. usePointing only calls pick() on a menu screen, never on the confirm
  // screen or the help countdown.
  const dwellOn = useSyncExternalStore(gazeTuning.subscribe, gazeTuning.get).dwell
  const input = useSocket('/ws/input', { enabled: started && dwellOn && !onboarding })
  const pick = (seq: number) => {
    const v = shown.current
    if (onboardingRef.current || v.kind !== 'menu' || v.screen.seq !== seq || v.screen.loading) return false
    return input.send({ type: 'CLENCH', t: Date.now() / 1000, strength: 1.0 })
  }
  usePointing({ mode, started, connected, screen, send, range: range ?? DEFAULT_RANGE, paused: calibrating || eyeCalibrating || onboarding, margin, pick })
  useComputerPointing({ state: computer, mode, connected, send, range: range ?? DEFAULT_RANGE,
    savedRange: range, voiceSource, paused: calibrating || computerCalibrating || eyeCalibrating, margin, pick: (seq, tile) => {
      const current = computerShown.current
      if (!current?.active || current.seq !== seq || current.paused || shown.current.kind !== 'computer') return false
      return send({ type: 'COMPUTER_POINT', seq, tile, source: 'gaze', found: true, status: 'tracking', t: Date.now()/1000, pick: true })
    } })

  // Onboarding (eyes, headband, a test clench) opens right after "Click to start"; the Dev panel's
  // Run setup opens it again. The shell holds its own calibration prompt back meanwhile.
  const openSetup = (on: boolean, muse = false) => {
    onboardingRef.current = on
    nativeBridge()?.setOnboarding?.(on)
    setOnboarding(on)
    const current = lastSettings.current
    // Finishing setup opens the Home board; Car mode is entered from its corner button (decisions #27).
    if (current) send({ ...current, pointing_mode: 'auto', onboarding: on, muse_enabled: muse })
  }
  useEffect(() => {
    const current = lastSettings.current
    if (current && !onboardingRef.current) send({ ...current, onboarding: carSetup })
  }, [carSetup, send])

  const start = (gesture = true) => {
    if (gesture) unlockSpeech()
    resetPending.current = true
    openSetup(true)
    setStarted(true)
  }

  // The tablet shell draws the car behind the page while the trip screen shows, framed for the
  // layout (car / split / map).
  useEffect(() => {
    if (!onboarding && !carSetup) showNativeCar(tripShown)
  }, [tripShown, onboarding, carSetup])
  useEffect(() => {
    setNativeCarLayout(displayLayout)
  }, [displayLayout])
  useEffect(() => () => showNativeCar(false), [])

  // See-through only where the shell draws behind (index.css paints the page black otherwise).
  useEffect(() => {
    const bg = seeThrough ? 'transparent' : ''
    document.documentElement.style.background = bg
    document.body.style.background = bg
  }, [seeThrough])

  return (
    <div className={`flex h-screen flex-col overflow-hidden text-white ${seeThrough ? 'bg-transparent' : 'bg-black'}`}>
      {!started && <StartOverlay onStart={start} lang={lang} />}
      {started && !onboarding && carSetup && trip && (
        <CarOnboarding lang={lang} paused={!connected || view.kind === 'help'} onDone={() => setCarSetup(false)} />
      )}
      {started && onboarding && <Onboarding lang={lang} paused={!connected || view.kind === 'help'} onDone={(muse) => openSetup(false, muse)} />}
      {started && onboarding && !connected && <div className="fixed inset-0 z-50 flex items-center justify-center bg-[#eaf6f4] p-8 text-center text-3xl text-[#007a72]">{STRINGS[lang].connecting}</div>}
      <div className={onboarding && view.kind !== 'help' ? 'hidden' : 'contents'}>
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

      {screen?.corner && !onboarding && !carSetup && (
        <CornerButton tile={screen.corner} index={screen.tiles.length} highlighted={screen.highlight === screen.tiles.length} onTap={tapTile} />
      )}
      {/* Never show a stale highlight while disconnected: the Core may have moved on. */}
      {(!connected || view.kind === 'waiting') && (
        <div className="flex flex-1 items-center justify-center text-4xl text-zinc-400">
          {STRINGS[lang].connecting}
        </div>
      )}
      {screen && !tripScreen && screen.screen === 'player' && media && (
        <>
          <MediaPlayer media={media} command={mediaCommand} />
          <ControlRow screen={screen} onTap={tapTile} />
          {pointing && <CursorDot />}
          {pointing && <DwellRing />}
        </>
      )}
      {tripScreen && !carSetup && (
        <>
          <TripView screen={tripScreen} anim={car.anim} phase={car.phase} tint={car.tint} onTap={tapTile} {...tripShared} />
          {pointing && <CursorDot />}
          {pointing && <DwellRing />}
        </>
      )}
      {screen && !tripScreen && !(screen.screen === 'player' && media) && (
        <>
          <Breadcrumb screen={screen} />
          <TileGrid screen={screen} onTap={tapTile} />
          {pointing && <CursorDot />}
          {pointing && <DwellRing />}
        </>
      )}
      {connected && view.kind === 'confirm' && (view.confirm.action === 'pull_over' || view.confirm.action === 'support' || view.confirm.action === 'route') && (
        <TripConfirm action={view.confirm.action} text={view.confirm.text} lang={lang} onConfirm={tapConfirm} onCancel={tapCancel} {...tripShared} />
      )}
      {connected && view.kind === 'confirm' && pointing && <CursorDot />}
      {connected && view.kind === 'confirm' && view.confirm.action !== 'pull_over' && view.confirm.action !== 'support' && view.confirm.action !== 'route' && (
        <ConfirmView confirm={view.confirm} lang={lang} onTap={tapConfirm} />
      )}
      {connected && view.kind === 'speaking' && trip && (
        <TripSpeaking lang={lang} text={view.text} tint={car.tint} {...tripShared} />
      )}
      {connected && view.kind === 'speaking' && !trip && <SpeakingView text={view.text} lang={lang} />}
      {connected && view.kind === 'help' && <HelpCountdownView countdown={view.countdown} lang={lang} onCancel={tapCancel} />}
      {connected && backPrompt && view.kind !== 'help' && (
        <BackPromptView key={backPrompt.at} kind={backPrompt.kind} ms={backPrompt.ms} lang={lang} onStay={tapCancel} />
      )}
      {connected && view.kind === 'computer' && (
        <main className="flex flex-1 flex-col items-center justify-center gap-6 px-12 text-center">
          <h1 className="text-6xl font-semibold">{lang === 'es' ? 'Modo computadora' : 'Computer mode'}</h1>
          <p className="max-w-3xl text-3xl text-zinc-300">
            {lang === 'es' ? 'El navegador está abierto. Mantén la mandíbula apretada para pedir ayuda.' :
              'The browser is open. Hold a clench to call for help.'}
          </p>
          <p className="text-2xl text-zinc-400">{lang === 'es' ? 'Menú del navegador → Salir para volver.' :
            'Browser menu → Exit to return.'}</p>
        </main>
      )}
      <ToastStack toasts={toasts} />
      <MusePanel />
      <InputLog />

      {calibrating && (
        <CalibrationOverlay lang={lang} onSaved={setRange} onClose={() => setCalibrating(false)} />
      )}
      {started && !eyeCalibrating && <EyeSetupOverlay lang={lang} onCalibrate={() => setEyeCalibrating(true)} />}
      {eyeCalibrating && <EyeCalibrationOverlay lang={lang} onClose={() => setEyeCalibrating(false)} />}
      <DevPanel
        voiceSource={voiceSource}
        headRange={range}
        cameraWanted={camera}
        onCalibrate={() => setCalibrating(true)}
        onCalibrateEyes={() => setEyeCalibrating(true)}
        onSetup={() => openSetup(true)}
      />
      </div>
    </div>
  )
}

/**
 * The corner button outside the six-tile grid (SCREEN.corner, tile index tiles.length): "Car mode" on
 * Home, "Home" in Car mode. Gaze or the head highlights it like a tile, the scan reaches it last, a
 * clench picks it. "Home" is large and set apart from the car controls so it is not picked by accident.
 */
function CornerButton({ tile, index, highlighted, onTap }: { tile: Tile; index: number; highlighted: boolean; onTap: (i: number) => void }) {
  const home = tile.id === 'corner.home'
  return (
    <div
      data-tile-index={index} // pointing measures it with the tiles (facetrack/)
      aria-current={highlighted}
      onClick={() => onTap(index)}
      className={[
        'fixed left-4 top-4 z-40 flex cursor-pointer select-none items-center justify-center rounded-3xl text-center font-bold',
        'transition-[transform,box-shadow] duration-200',
        home ? 'h-32 w-56 text-4xl' : 'h-28 w-52 text-3xl',
        highlighted
          ? 'scale-105 bg-white text-zinc-900 ring-[10px] ring-amber-500 shadow-2xl'
          : home
            ? 'bg-zinc-800/95 text-white ring-4 ring-white/70 shadow-xl'
            : 'bg-sky-700/95 text-white ring-4 ring-sky-300/70 shadow-xl',
      ].join(' ')}
    >
      {home ? `‹ ${tile.label}` : tile.label}
    </div>
  )
}
