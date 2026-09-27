// The board's one sound queue (decisions.md "Echo queue"). Pure: the player is passed in, so the
// order rules are unit tested without audio.
//
//   - Echoes (picked words) and clicks play in order, one after another, never cutting each other off.
//   - A phrase (the confirmed sentence) waits for what is queued before it, then plays.
//   - A system line (help) clears the queue and plays at once, interrupting whatever is playing.
//   - At most MAX_WAITING items wait. On overflow the oldest waiting echo or click is dropped (a
//     phrase only when nothing else is waiting) and it is logged.
//   - onEnd runs exactly once for every item added: finished, failed, dropped or interrupted.

export const MAX_WAITING = 6

export interface Queued {
  kind: 'phrase' | 'echo' | 'system' | 'click'
}

export interface Player<T> {
  /** Start `item`; call `done` once when it ends (finished or failed). */
  start(item: T, done: () => void): void
  /** Stop what is playing now without calling its `done`. */
  stop(): void
}

export class SoundQueue<T extends Queued> {
  private waiting: T[] = []
  private current: { item: T } | null = null
  private readonly player: Player<T>
  private readonly onEnd: (item: T) => void
  private readonly max: number

  constructor(player: Player<T>, onEnd: (item: T) => void, max = MAX_WAITING) {
    this.player = player
    this.onEnd = onEnd
    this.max = max
  }

  /** What is playing now (null = silent). */
  get playing(): T | null {
    return this.current?.item ?? null
  }

  /** The items waiting to play, in order. */
  get queued(): readonly T[] {
    return this.waiting
  }

  add(item: T): void {
    if (item.kind === 'system') {
      const dropped = this.waiting
      this.waiting = []
      const playing = this.current
      this.current = null
      if (playing) this.player.stop()
      for (const d of [...(playing ? [playing.item] : []), ...dropped]) this.onEnd(d)
    }
    this.waiting.push(item)
    if (this.waiting.length > this.max) {
      const i = this.waiting.findIndex((w) => w.kind !== 'phrase')
      const [old] = this.waiting.splice(i >= 0 ? i : 0, 1)
      console.warn(`sound queue full (${this.max} waiting): dropped the oldest ${old.kind}`, old)
      this.onEnd(old)
    }
    this.next()
  }

  private next(): void {
    if (this.current || this.waiting.length === 0) return
    const entry = { item: this.waiting.shift() as T }
    this.current = entry
    this.player.start(entry.item, () => {
      if (this.current !== entry) return // interrupted: already ended
      this.current = null
      this.onEnd(entry.item)
      this.next()
    })
  }
}
