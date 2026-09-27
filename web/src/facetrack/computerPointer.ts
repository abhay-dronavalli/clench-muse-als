import type { ComputerPoint, ComputerState } from '../contracts'
import type { PointSample } from './source'
import { TilePointer, type PointerTuning } from './tilePointer'

/** Same filtered tile selection as the board, using the foreground browser's measured rectangles. */
export class ComputerPointer {
  private pointer: TilePointer
  private seq = -1
  private source = ''
  private lastSent = -Infinity
  private lastTile: number | null = null
  private lastFound = false

  constructor(tuning: PointerTuning) { this.pointer = new TilePointer(tuning) }

  update(sample: PointSample, state: ComputerState, tuning: PointerTuning, status: ComputerPoint['status']): ComputerPoint | null {
    if (!state.active) return null
    const changed = this.seq !== state.seq || this.source !== sample.source
    if (changed) {
      if (this.source !== sample.source) this.pointer.lost()
      this.seq = state.seq
      this.source = sample.source
      this.pointer.reset(state.highlight)
      this.lastSent = -Infinity
    }
    this.pointer.tuning = tuning
    let tile: number | null = null
    if (sample.found && sample.point && !state.paused) {
      tile = this.pointer.update(sample.point, sample.t, state.tiles).tile
    } else this.pointer.lost()
    if (!changed && tile === this.lastTile && sample.found === this.lastFound && sample.t - this.lastSent < 200) return null
    this.lastTile = tile
    this.lastFound = sample.found
    this.lastSent = sample.t
    return { type: 'COMPUTER_POINT', seq: state.seq, source: sample.source === 'gaze' ? 'gaze' : 'webcam',
      tile, found: sample.found, status, t: Date.now() / 1000 }
  }
}
