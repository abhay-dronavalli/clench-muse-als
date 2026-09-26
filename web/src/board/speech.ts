import type { Lang } from '../contracts'

const LANG_TAG: Record<Lang, string> = { en: 'en-US', es: 'es-US' }

// Keep a reference to the utterance being spoken: Chrome can garbage-collect it mid-sentence and
// then never fire onend.
let current: SpeechSynthesisUtterance | null = null

function available(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window
}

/**
 * Browsers block audio until the user interacts with the page. Call this from a click handler:
 * speaking an empty utterance unlocks speech for the rest of the session.
 */
export function unlockSpeech(): void {
  if (!available()) return
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

/** Speak `text`; `onDone` runs exactly once, when speech ends or fails. */
export function speak(text: string, lang: Lang, onDone: () => void): void {
  if (!available()) {
    console.warn('speechSynthesis not available')
    onDone()
    return
  }
  // Drop the previous utterance's callbacks so cancelling it does not report "done" for this one.
  if (current) {
    current.onend = null
    current.onerror = null
  }
  window.speechSynthesis.cancel()

  const tag = LANG_TAG[lang]
  const u = new SpeechSynthesisUtterance(text)
  u.lang = tag
  const voice = pickVoice(tag)
  if (voice) u.voice = voice

  let done = false
  const finish = () => {
    if (done) return
    done = true
    if (current === u) current = null
    onDone()
  }
  u.onend = finish
  u.onerror = (e) => {
    console.warn('speech failed:', e.error)
    finish()
  }
  current = u
  window.speechSynthesis.speak(u)
}

/** Play an audio file (PLAY_AUDIO, later cloud voices); `onDone` runs exactly once. */
export function playAudio(url: string, onDone: () => void): void {
  let done = false
  const finish = () => {
    if (done) return
    done = true
    onDone()
  }
  const audio = new Audio(url)
  audio.onended = finish
  audio.onerror = finish
  audio.play().catch(finish)
}
