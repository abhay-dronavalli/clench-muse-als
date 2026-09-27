import type { Lang, UtteranceKind } from '../contracts'
import { SoundQueue } from './queue'

/**
 * Everything the board says (SPEAK and PLAY_AUDIO) goes through say(), and the "Other..." click
 * (CLICK) through click(): one queue, one audio player.
 * The order rules live in queue.ts:
 *
 *   - Echoes (picked words) play in order, one after another, never cutting each other off.
 *   - A phrase waits for the echoes queued before it, then plays.
 *   - A system line clears the queue and plays at once.
 *   - At most 6 items wait; on overflow the oldest is dropped (logged).
 *
 * And here:
 *   - An echo whose audio has not started playing within 300 ms is said with browser speech
 *     instead, in its place in the queue (the Core keeps making the audio for next time).
 *   - If the audio file fails to load or play, the same text is said with browser speech.
 *   - Echo plays at 70% volume, phrase and system at 100%.
 *   - onEnd runs exactly once for every utterance: finished, failed, dropped or interrupted.
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

/** How long an echo's audio may take to start before browser speech says the word instead. */
export const ECHO_START_MS = 300
const CLICK_MS = 90 // the click's length; the next sound starts after it

const LANG_TAG: Record<Lang, string> = { en: 'en-US', es: 'es-US' }
const VOLUME: Record<UtteranceKind, number> = { echo: 0.7, phrase: 1, system: 1 }

interface Pending {
  kind: UtteranceKind
  u: Utterance
  onEnd: (u: Utterance) => void
  onSource: (s: VoiceSource) => void
}

type Sound = Pending | { kind: 'click' }

let player: HTMLAudioElement | null = null
// Keep a reference to the utterance being spoken: Chrome can garbage-collect it mid-sentence and
// then never fire onend.
let current: SpeechSynthesisUtterance | null = null
let run = 0 // bumps on every start and stop, so callbacks of an older item do nothing
let clicks: AudioContext | null = null

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
  try {
    clicks ??= new AudioContext()
    void clicks.resume()
  } catch (e) {
    console.warn('WebAudio not available: no click for "Other..."', e)
  }
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
  run++
  if (player) {
    player.onended = null
    player.onerror = null
    player.onplaying = null
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

/** A short soft click at echo volume: a sine blip falling from 1.6 to 0.8 kHz, 60 ms. */
function playClick(done: () => void): void {
  const mine = run
  const ctx = clicks
  if (ctx) {
    const t = ctx.currentTime
    const osc = ctx.createOscillator()
    const gain = ctx.createGain()
    osc.type = 'sine'
    osc.frequency.setValueAtTime(1600, t)
    osc.frequency.exponentialRampToValueAtTime(800, t + 0.05)
    gain.gain.setValueAtTime(0.0001, t)
    gain.gain.exponentialRampToValueAtTime(0.2 * VOLUME.echo, t + 0.005)
    gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.06)
    osc.connect(gain).connect(ctx.destination)
    osc.start(t)
    osc.stop(t + 0.07)
  }
  window.setTimeout(() => run === mine && done(), CLICK_MS)
}

/** Play one sound; `done` runs once when it ends or fails (never after a stop). */
function start(sound: Sound, done: () => void): void {
  stopAll()
  if (sound.kind === 'click') {
    playClick(done)
    return
  }
  const { u, onSource } = sound
  const mine = ++run
  const live = () => run === mine
  let fellBack = false
  let startTimer: number | undefined
  const finish = () => {
    window.clearTimeout(startTimer)
    if (live()) done()
  }
  const withBrowser = () => {
    if (!live() || fellBack) return
    fellBack = true
    window.clearTimeout(startTimer)
    stopAll()
    run = mine // still this item: its browser speech must be able to finish it
    onSource('Browser')
    speakWithBrowser(u.text, u.lang, VOLUME[u.kind], finish)
  }

  if (!u.audio) {
    withBrowser()
    return
  }
  const { url, cached } = u.audio
  onSource(cached ? 'ElevenLabs (cached)' : 'ElevenLabs')
  const audio = audioPlayer()
  audio.onended = finish
  audio.onerror = () => {
    console.warn('audio failed, using browser speech:', url)
    withBrowser()
  }
  audio.onplaying = () => window.clearTimeout(startTimer)
  if (u.kind === 'echo') {
    // A picked word that is not playing within 300 ms is said by the browser, in its place.
    startTimer = window.setTimeout(() => {
      console.info(`echo audio not playing after ${ECHO_START_MS} ms, using browser speech:`, u.text)
      withBrowser()
    }, ECHO_START_MS)
  }
  audio.volume = VOLUME[u.kind]
  audio.src = url
  audio.play().catch((e: unknown) => {
    if (!live()) return // stopped or replaced meanwhile (AbortError)
    console.warn('audio play failed, using browser speech:', e)
    withBrowser()
  })
}

const queue = new SoundQueue<Sound>({ start, stop: stopAll }, (s) => {
  if (s.kind !== 'click') s.onEnd(s.u)
})

/**
 * Queue `u` following the rules at the top of this file. `onEnd` runs once when it has ended (or was
 * dropped or interrupted); `onSource` reports where the sound actually comes from.
 */
export function say(u: Utterance, onEnd: (u: Utterance) => void, onSource: (s: VoiceSource) => void): void {
  queue.add({ kind: u.kind, u, onEnd, onSource })
}

/** Queue the short soft click for a picked "Other..." (in order with the echoes). */
export function click(): void {
  queue.add({ kind: 'click' })
}
