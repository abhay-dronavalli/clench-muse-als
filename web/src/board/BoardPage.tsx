import { useState } from 'react'
import type { Confirm, Lang, Message, Screen } from '../contracts'
import DevPanel from '../dev/DevPanel'
import { StatusDot } from '../lib/StatusDot'
import { useSocket, type Send } from '../lib/useSocket'
import { say, unlockSpeech, type Utterance, type VoiceSource } from './speech'
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

/**
 * Patient board. It is "dumb" (PRD A3.3): it draws what the Core sends and reports back only
 * READY and AUDIO_DONE. All decisions, including the highlight, stay in the Core.
 *
 * Speech: only a phrase (the confirmed sentence) shows the speaking screen. Echoes (picked tiles)
 * and system lines play over whatever is on screen. AUDIO_DONE goes back for phrases and system
 * lines, never for echoes.
 */
export default function BoardPage() {
  const [started, setStarted] = useState(false)
  const [view, setView] = useState<View>({ kind: 'waiting' })
  const [lang, setLang] = useState<Lang>('en')
  const [voiceSource, setVoiceSource] = useState<VoiceSource | null>(null)
  const { toasts, push } = useToasts()

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
      case 'ACTION_RESULT':
        push(toastFor(msg, lang))
        break
      default:
        console.warn('board ignored', msg.type)
    }
  }

  // The board goes live only after the click that unlocks speech, so no SPEAK can arrive muted.
  const { status } = useSocket('/ws/board', {
    enabled: started,
    onOpen: (send) => send({ type: 'READY' }),
    onMessage,
  })

  const start = () => {
    unlockSpeech()
    setStarted(true)
  }

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-black text-white">
      {!started && <StartOverlay onStart={start} />}
      <div className="fixed right-4 top-4 z-30">
        <StatusDot status={started ? status : 'closed'} label="Core" />
      </div>

      {/* Never show a stale highlight while disconnected: the Core may have moved on. */}
      {(status !== 'open' || view.kind === 'waiting') && (
        <div className="flex flex-1 items-center justify-center text-4xl text-zinc-400">
          {STRINGS[lang].connecting}
        </div>
      )}
      {status === 'open' && view.kind === 'menu' && (
        <>
          <Breadcrumb screen={view.screen} />
          <TileGrid screen={view.screen} />
        </>
      )}
      {status === 'open' && view.kind === 'confirm' && <ConfirmView confirm={view.confirm} lang={lang} />}
      {status === 'open' && view.kind === 'speaking' && <SpeakingView text={view.text} lang={lang} />}
      {status === 'open' && view.kind === 'help' && <HelpCountdownView countdown={view.countdown} lang={lang} />}
      <ToastStack toasts={toasts} />

      <DevPanel lang={lang} voiceSource={voiceSource} />
    </div>
  )
}
