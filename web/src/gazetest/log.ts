// Gaze test results, per person, kept in this browser (localStorage) with JSON and CSV export.
// Nothing goes to the Core. Pure except load/save, unit tested.

import type { GazeTuning } from '../facetrack/gazeTuning'
import { summarize, type Summary, type Trial } from './run'

export type TestSource = 'gaze' | 'head' | 'mouse'

export interface RunRecord {
  id: string
  person: string
  /** ISO time the run finished */
  at: string
  source: TestSource
  /** "3x2" landscape (3 across), "2x3" portrait */
  layout: '3x2' | '2x3'
  tuning: GazeTuning
  margin: number
  /** the SDK's own filter, when the tablet shell is there (null = no shell) */
  sdkFilter: boolean | null
  /** blinks the shell reported during the run (not used to pick; recorded to judge DOUBLE_BLINK later) */
  blinks: number
  trials: Trial[]
  summary: Summary
}

const KEY = 'clench.gazeTest.runs'

export function load(): RunRecord[] {
  try {
    const v: unknown = JSON.parse(window.localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(v) ? (v as RunRecord[]) : []
  } catch {
    return []
  }
}

export function save(runs: RunRecord[]): boolean {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(runs))
    return true
  } catch {
    return false
  }
}

export function makeRecord(r: Omit<RunRecord, 'summary' | 'id'>): RunRecord {
  return { ...r, id: `${r.at}-${r.person}`, summary: summarize(r.trials) }
}

export interface PersonStats {
  person: string
  runs: number
  /** the best run's hit rate (a person passes with one run >= 90%) */
  best: number
  /** all trials together */
  hitRate: number
  avgMs: number | null
}

/** One line per person over the given runs (filter them by source first). */
export function byPerson(runs: RunRecord[]): PersonStats[] {
  const groups = new Map<string, RunRecord[]>()
  for (const r of runs) groups.set(r.person, [...(groups.get(r.person) ?? []), r])
  return [...groups.entries()]
    .map(([person, rs]) => {
      const all = summarize(rs.flatMap((r) => r.trials))
      return {
        person,
        runs: rs.length,
        best: Math.max(...rs.map((r) => r.summary.hitRate)),
        hitRate: all.hitRate,
        avgMs: all.avgMs,
      }
    })
    .sort((a, b) => a.person.localeCompare(b.person))
}

const CSV_HEAD = [
  'at', 'person', 'source', 'layout', 'one_euro', 'min_cutoff', 'beta', 'hold_ms', 'margin', 'sdk_filter',
  'trials', 'hits', 'hit_rate', 'avg_ms', 'wrong', 'blinks',
]

const cell = (v: unknown) => {
  const s = v === null || v === undefined ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}

/** One row per run. */
export function toCsv(runs: RunRecord[]): string {
  const rows = runs.map((r) =>
    [
      r.at, r.person, r.source, r.layout, r.tuning.oneEuro, r.tuning.euro.minCutoff, r.tuning.euro.beta,
      r.tuning.holdMs, r.margin, r.sdkFilter, r.summary.trials, r.summary.hits, r.summary.hitRate.toFixed(2),
      r.summary.avgMs === null ? '' : Math.round(r.summary.avgMs), r.summary.wrong, r.blinks,
    ].map(cell).join(','),
  )
  return [CSV_HEAD.join(','), ...rows].join('\n') + '\n'
}
