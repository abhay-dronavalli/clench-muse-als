import type { Lang, UtteranceKind } from '../contracts'

/**
 * Everything the board says (SPEAK and PLAY_AUDIO) goes through say(): one audio player, reused.
 *
 *   - A new echo interrupts an older echo. An echo that arrives while a phrase or system line is
 *     playing is dropped: it never cuts the person's sentence.
 *   - A phrase or system line interrupts whatever is playing.
 *   - Echo plays at 70% volume, phrase and system at 100%.
 *   - If the audio file fails to load or play, the same text is said with browser speech.
 *   - onEnd runs exactly once for every item that started: finished, failed or interrupted.
 */

export type VoiceSource = 'ElevenLabs (cached)' | 'ElevenLabs' | 'Browser'

export interface Utterance {
  id: string
  kind: UtteranceKind
  text: string
  lang: Lang
  /** ElevenLabs audio from PLAY_AUDIO; absent for SPEAK (browser speech) */
  audio?: { url: string; cached: boolean }
}

const LANG_TAG: Record<Lang, string> = { en: 'en-US', es: 'es-US' }
const VOLUME: Record<UtteranceKind, number> = { echo: 0.7, phrase: 1, system: 1 }

interface Playing {
  u: Utterance
  end: () => void
}

let playing: Playing | null = null
let player: HTMLAudioElement | null = null
// Keep a reference to the utterance being spoken: Chrome can garbage-collect it mid-sentence and
// then never fire onend.
let current: SpeechSynthesisUtterance | null = null

function speechAvailable(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

function audioPlayer(): HTMLAudioElement {
  player ??= new Audio()
  return player
}

/**
 * Browsers block audio until the user interacts with the page. Call this from a click handler:
 * speaking an empty utterance unlocks speech, and the click lets the audio player play later.
 */
export function unlockSpeech(): void {
  audioPlayer()
  if (!speechAvailable()) return
  window.speechSynthesis.getVoices() // start loading voices early
  const u = new SpeechSynthesisUtterance('')
  u.volume = 0
  window.speechSynthesis.speak(u)
}

/** An installed voice for the language: exact region first (es-US), then any es-* voice. */
function pickVoice(tag: string): SpeechSynthesisVoice | undefined {
  const voices = window.speechSynthesis.getVoices()
  const norm = (v: SpeechSynthesisVoice) => v.lang.replace('_', '-').toLowerCase()
  const want = tag.toLowerCase()
  const base = want.slice(0, 2)
  return voices.find((v) => norm(v) === want) ?? voices.find((v) => norm(v).startsWith(base))
}

/** Stop the audio player and browser speech without firing any of their callbacks. */
function stopAll(): void {
  if (player) {
    player.onended = null
    player.onerror = null
    player.pause()
  }
  if (speechAvailable()) {
    if (current) {
      current.onend = null
      current.onerror = null
      current = null
    }
    window.speechSynthesis.cancel()
  }
}

/** Browser speech; `onDone` runs once, when speech ends or fails. */
function speakWithBrowser(text: string, lang: Lang, volume: number, onDone: () => void): void {
  if (!speechAvailable()) {
    console.warn('speechSynthesis not available')
    onDone()
    return
  }
  const tag = LANG_TAG[lang]
  const u = new SpeechSynthesisUtterance(text)
  u.lang = tag
  u.volume = volume
  const voice = pickVoice(tag)
  if (voice) u.voice = voice
  u.onend = onDone
  u.onerror = (e) => {
    console.warn('speech failed:', e.error)
    onDone()
  }
  current = u
  window.speechSynthesis.speak(u)
}

/**
 * Say `u` following the rules at the top of this file. Returns false when it was dropped (an echo
 * during a phrase or system line). `onSource` reports where the sound actually comes from.
 */
export function say(u: Utterance, onEnd: (u: Utterance) => void, onSource: (s: VoiceSource) => void): boolean {
  if (u.kind === 'echo' && playing && playing.u.kind !== 'echo') return false

  const previous = playing
  playing = null
  stopAll()
  previous?.end() // interrupted: its AUDIO_DONE still goes out

  let ended = false
  const entry: Playing = {
    u,
    end: () => {
      if (ended) return
      ended = true
      if (playing === entry) playing = null
      onEnd(u)
    },
  }
  playing = entry
  const isCurrent = () => playing === entry
  const finish = () => {
    if (isCurrent()) entry.end()
  }

  let fellBack = false
  const withBrowser = () => {
    if (!isCurrent() || fellBack) return
    fellBack = true
    stopAll()
    onSource('Browser')
    speakWithBrowser(u.text, u.lang, VOLUME[u.kind], finish)
  }

  if (!u.audio) {
    withBrowser()
    return true
  }
  const { url, cached } = u.audio
  onSource(cached ? 'ElevenLabs (cached)' : 'ElevenLabs')
  const audio = audioPlayer()
  audio.onended = finish
  audio.onerror = () => {
    console.warn('audio failed, using browser speech:', url)
    withBrowser()
  }
  audio.volume = VOLUME[u.kind]
  audio.src = url
  audio.play().catch((e: unknown) => {
    if (!isCurrent()) return // a newer item interrupted this one (AbortError)
    console.warn('audio play failed, using browser speech:', e)
    withBrowser()
  })
  return true
}
