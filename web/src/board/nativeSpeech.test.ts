import { afterEach, describe, expect, it, vi } from 'vitest'
import type { NativeEvent } from '../facetrack/native'
import { guardMs, speakNative, type SpeechBridge } from './nativeSpeech'

function setup(taken = true) {
  const listeners = new Set<(e: NativeEvent) => void>()
  const subscribe = (fn: (e: NativeEvent) => void) => {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }
  const spoken: { id: string; text: string; lang: string; volume: number }[] = []
  const bridge: SpeechBridge & { stops: number } = {
    stops: 0,
    speak(id, text, lang, volume) {
      spoken.push({ id, text, lang, volume })
      return taken
    },
    stopSpeaking() {
      this.stops++
    },
  }
  const emit = (e: NativeEvent) => listeners.forEach((fn) => fn(e))
  return { bridge, subscribe, spoken, emit, listeners }
}

describe('speakNative (the tablet voice)', () => {
  afterEach(() => vi.useRealTimers())

  it('finishes once when the shell says the line is done', () => {
    const { bridge, subscribe, spoken, emit, listeners } = setup()
    const done = vi.fn()
    expect(speakNative(bridge, subscribe, 'Hola', 'es-US', 0.7, done)).not.toBeNull()
    expect(spoken).toEqual([{ id: spoken[0].id, text: 'Hola', lang: 'es-US', volume: 0.7 }])
    emit({ type: 'speech', id: 'someone-else', state: 'done' })
    expect(done).not.toHaveBeenCalled()
    emit({ type: 'speech', id: spoken[0].id, state: 'done' })
    emit({ type: 'speech', id: spoken[0].id, state: 'done' })
    expect(done).toHaveBeenCalledTimes(1)
    expect(listeners.size).toBe(0)
  })

  it('moves on after an error too', () => {
    const { bridge, subscribe, spoken, emit } = setup()
    const done = vi.fn()
    speakNative(bridge, subscribe, 'Hi', 'en-US', 1, done)
    emit({ type: 'speech', id: spoken[0].id, state: 'error', detail: 'no engine' })
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('never hangs the queue when the shell never answers', () => {
    vi.useFakeTimers()
    const { bridge, subscribe } = setup()
    const done = vi.fn()
    speakNative(bridge, subscribe, 'Hello', 'en-US', 1, done)
    vi.advanceTimersByTime(guardMs('Hello') - 1)
    expect(done).not.toHaveBeenCalled()
    vi.advanceTimersByTime(1)
    expect(done).toHaveBeenCalledTimes(1)
  })

  it('cancel stops the voice and never calls onDone', () => {
    vi.useFakeTimers()
    const { bridge, subscribe, spoken, emit } = setup()
    const done = vi.fn()
    const cancel = speakNative(bridge, subscribe, 'Hello', 'en-US', 1, done)!
    cancel()
    expect(bridge.stops).toBe(1)
    emit({ type: 'speech', id: spoken[0].id, state: 'done' })
    vi.advanceTimersByTime(guardMs('Hello'))
    expect(done).not.toHaveBeenCalled()
  })

  it('returns null when the shell cannot take the line, without calling onDone', () => {
    vi.useFakeTimers()
    const { bridge, subscribe, listeners } = setup(false)
    const done = vi.fn()
    expect(speakNative(bridge, subscribe, 'Hello', 'en-US', 1, done)).toBeNull()
    vi.advanceTimersByTime(guardMs('Hello'))
    expect(done).not.toHaveBeenCalled()
    expect(listeners.size).toBe(0)
  })
})
