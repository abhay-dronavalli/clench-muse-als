import type { NativeEvent } from '../facetrack/native'

/**
 * Speech through the tablet shell's Android voice: WebView has no speechSynthesis, so on the tablet
 * this takes the place of browser speech (speech.ts). The shell answers every speak() with one
 * 'speech' event carrying the same id; a guard timer finishes the line anyway if it never comes.
 */

export interface SpeechBridge {
  speak(id: string, text: string, lang: string, volume: number): boolean
  stopSpeaking(): void
}

type Subscribe = (fn: (e: NativeEvent) => void) => () => void

/** Longest a line may take before we stop waiting for the shell: 3 s plus 150 ms a character. */
export function guardMs(text: string): number {
  return 3000 + 150 * text.length
}

let seq = 0

/**
 * Say `text`; `onDone` runs exactly once (spoken, failed, or given up on). Returns a cancel function
 * that stops the voice and forgets the line without calling `onDone`, or null if the shell could not
 * take the line (the caller moves on).
 */
export function speakNative(
  bridge: SpeechBridge,
  subscribe: Subscribe,
  text: string,
  lang: string,
  volume: number,
  onDone: () => void,
  timers: Pick<typeof globalThis, 'setTimeout' | 'clearTimeout'> = globalThis,
): (() => void) | null {
  const id = `s${++seq}`
  let over = false
  const end = () => {
    if (over) return
    over = true
    unsubscribe()
    timers.clearTimeout(guard)
  }
  const unsubscribe = subscribe((e) => {
    if (e.type !== 'speech' || e.id !== id || over) return
    if (e.state === 'error') console.warn('tablet voice failed:', e.detail ?? '')
    end()
    onDone()
  })
  const guard = timers.setTimeout(() => {
    if (over) return
    console.warn('tablet voice did not answer, moving on:', text)
    end()
    onDone()
  }, guardMs(text))
  let taken = false
  try {
    taken = bridge.speak(id, text, lang, volume)
  } catch (e) {
    console.warn('tablet voice unavailable:', e)
  }
  if (!taken) {
    end()
    return null
  }
  return () => {
    if (over) return
    end()
    try {
      bridge.stopSpeaking()
    } catch {
      // the shell went away; nothing to stop
    }
  }
}
