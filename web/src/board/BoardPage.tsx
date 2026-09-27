import { useEffect, useRef, useState } from 'react'
import type { Confirm, HeadRange, Lang, Message, PointingMode, Screen } from '../contracts'
import DevPanel from '../dev/DevPanel'
import { CalibrationOverlay } from '../facetrack/CalibrationOverlay'
import { loadHeadRange } from '../facetrack/headRange'
import { CameraLight, CameraNotice, CursorDot, GazeNotice, PointerBadge } from '../facetrack/indicators'
import { DEFAULT_RANGE } from '../facetrack/pose'
import { STICKY_MARGIN } from '../facetrack/tiles'
import { boardPoints, headCamera, usePointing } from '../facetrack/usePointing'
import { StatusDot } from '../lib/StatusDot'
import { useSocket, type Send } from '../lib/useSocket'
import { click, say, unlockSpeech, type Utterance, type VoiceSource } from './speech'
import { STRINGS } from './strings'
import { toastFor, useToasts } from './toast'
import { ToastStack } from './ToastStack'
import { Breadcrumb, ConfirmView, HelpCountdownView, SpeakingView, StartOverlay, TileGrid } from './views'

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
  const [view, setView] = useState<View>({ kind: 'waiting' })
  const [lang, setLang] = useState<Lang>('en')
  const [mode, setMode] = useState<PointingMode | null>(null)
  const [margin, setMargin] = useState(STICKY_MARGIN) // SETTINGS tile_switch_margin
  const [voiceSource, setVoiceSource] = useState<VoiceSource | null>(null)
  const [range, setRange] = useState<HeadRange | null>(null) // null = not calibrated: defaults
  const [calibrating, setCalibrating] = useState(false)
  const { toasts, push } = useToasts()
  // "Click to start" sends RESET once (Home, first tile); a later reconnect only sends READY.
  const resetPending = useRef(false)

  const onMessage = (msg: Message, send: Send) => {
    switch (msg.type) {
      case 'SCREEN':
        setLang(msg.lang)
        if (msg.screen === 'help_countdown') setView({ kind: 'help', countdown: msg.countdown ?? 0 })
        else if (msg.screen === 'computer') setView({ kind: 'computer' })
        else setView({ kind: 'menu', screen: msg })
        break
      case 'CONFIRM':
        setView({ kind: 'confirm', confirm: msg })
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
      case 'ACTION_RESULT':
        push(toastFor(msg, lang))
        break
      case 'SETTINGS':
        // The language and the pointing mode (camera on or off); the dev panel shows the rest.
        if (msg.lang) setLang(msg.lang)
        setMode(msg.pointing_mode)
        if (msg.tile_switch_margin !== undefined) setMargin(msg.tile_switch_margin)
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

  const camera = started && headCamera(mode)
  const pointing = started && boardPoints(mode)
  const screen = connected && view.kind === 'menu' ? view.screen : null
  usePointing({ mode, started, connected, screen, send, range: range ?? DEFAULT_RANGE, paused: calibrating, margin })

  const start = () => {
    unlockSpeech()
    resetPending.current = true
    setStarted(true)
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-black text-white">
      {!started && <StartOverlay onStart={start} />}
      <div className="fixed right-4 top-4 z-30 flex flex-col items-end gap-2">
        <div className="flex items-center gap-3">
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
      {screen && (
        <>
          <Breadcrumb screen={screen} />
          <TileGrid screen={screen} />
          {pointing && <CursorDot />}
        </>
      )}
      {connected && view.kind === 'confirm' && <ConfirmView confirm={view.confirm} lang={lang} />}
      {connected && view.kind === 'speaking' && <SpeakingView text={view.text} lang={lang} />}
      {connected && view.kind === 'help' && <HelpCountdownView countdown={view.countdown} lang={lang} />}
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
