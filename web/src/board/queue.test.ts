import { describe, expect, it, vi } from 'vitest'
import { MAX_WAITING, SoundQueue, type Player, type Queued } from './queue'

interface Item extends Queued {
  text: string
}

/** A player that only records: the test ends items with finish(). */
function setup(max = MAX_WAITING) {
  const started: string[] = []
  const ended: string[] = []
  const stops: string[] = []
  let done: (() => void) | null = null
  let current: string | null = null
  const player: Player<Item> = {
    start(item, d) {
      started.push(item.text)
      current = item.text
      done = d
    },
    stop() {
      if (current) stops.push(current)
    },
  }
  const q = new SoundQueue<Item>(player, (i) => ended.push(i.text), max)
  const finish = () => done?.()
  return { q, started, ended, stops, finish }
}

const echo = (text: string): Item => ({ kind: 'echo', text })
const phrase = (text: string): Item => ({ kind: 'phrase', text })
const system = (text: string): Item => ({ kind: 'system', text })
const click = (text = 'click'): Item => ({ kind: 'click', text })

describe('SoundQueue', () => {
  it('plays echoes in order without cutting any off', () => {
    const { q, started, ended, stops, finish } = setup()
    q.add(echo('I need'))
    q.add(echo('Pain'))
    q.add(click())
    q.add(echo('Back'))
    expect(started).toEqual(['I need'])
    for (let i = 0; i < 4; i++) finish()
    expect(started).toEqual(['I need', 'Pain', 'click', 'Back'])
    expect(ended).toEqual(['I need', 'Pain', 'click', 'Back'])
    expect(stops).toEqual([])
    expect(q.playing).toBeNull()
  })

  it('a phrase waits for the echoes queued before it', () => {
    const { q, started, finish } = setup()
    q.add(echo('A lot'))
    q.add(phrase('My back hurts a lot.'))
    expect(started).toEqual(['A lot'])
    finish()
    expect(started).toEqual(['A lot', 'My back hurts a lot.'])
  })

  it('a system line clears the queue and plays at once', () => {
    const { q, started, ended, stops } = setup()
    q.add(echo('I need'))
    q.add(echo('Pain'))
    q.add(system('Calling for help.'))
    expect(stops).toEqual(['I need'])
    expect(started).toEqual(['I need', 'Calling for help.'])
    expect(ended).toEqual(['I need', 'Pain']) // both ended (AUDIO_DONE is only sent for non-echoes)
  })

  it('an interrupted item never ends twice', () => {
    const started: string[] = []
    const ended: string[] = []
    const dones: (() => void)[] = []
    const q = new SoundQueue<Item>(
      { start: (i, d) => (started.push(i.text), dones.push(d)), stop: () => {} },
      (i) => ended.push(i.text),
    )
    q.add(phrase('Hello'))
    q.add(system('Help'))
    dones[0]() // the interrupted phrase's player calls back late
    expect(ended).toEqual(['Hello'])
    dones[1]()
    expect(ended).toEqual(['Hello', 'Help'])
  })

  it('caps the waiting items and drops the oldest echo', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const { q, started, ended, finish } = setup(3)
    q.add(echo('playing'))
    q.add(phrase('keep me'))
    q.add(echo('one'))
    q.add(echo('two'))
    q.add(echo('three')) // 4 waiting > 3: "one" is the oldest echo
    expect(ended).toEqual(['one'])
    expect(q.queued.map((i) => i.text)).toEqual(['keep me', 'two', 'three'])
    expect(warn).toHaveBeenCalledOnce()
    for (let i = 0; i < 4; i++) finish()
    expect(started).toEqual(['playing', 'keep me', 'two', 'three'])
    warn.mockRestore()
  })

  it('a player that ends at once moves straight on', () => {
    const ended: string[] = []
    const q = new SoundQueue<Item>({ start: (_i, d) => d(), stop: () => {} }, (i) => ended.push(i.text))
    q.add(echo('a'))
    q.add(echo('b'))
    expect(ended).toEqual(['a', 'b'])
    expect(q.playing).toBeNull()
  })
})
