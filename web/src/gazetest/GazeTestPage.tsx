import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { HeadRange } from '../contracts'
import { gaze } from '../facetrack/gaze'
import { DEFAULT_GAZE_TUNING, gazePointerTuning, LIMITS, type GazeTuning } from '../facetrack/gazeTuning'
import { loadHeadRange } from '../facetrack/headRange'
import { nativeBridge, nativeEvents, nativeGazeActive, type NativeEvent } from '../facetrack/native'
import { DEFAULT_RANGE, type ScreenPoint } from '../facetrack/pose'
import { fromHead, type PointSample } from '../facetrack/source'
import { gazeTuning } from '../facetrack/stores'
import { DwellTimer, HEAD_TUNING, TilePointer } from '../facetrack/tilePointer'
import { MAX_STICKY_MARGIN, STICKY_MARGIN } from '../facetrack/tiles'
import { tracker } from '../facetrack/tracker'
import { measureTiles } from '../facetrack/usePointing'
import { useTrackerStatus } from '../facetrack/useTrackerStatus'
import { byPerson, load, makeRecord, save, toCsv, type RunRecord, type TestSource } from './log'
import { makeTargets, summarize, TARGETS, TestRun, TIMEOUT_MS } from './run'

const TILE_ATTR = 'data-test-tile'
const PERSON_KEY = 'clench.gazeTest.person'
const TICK_MS = 50 // the run's clock also advances without samples (eyes lost, tracker stopped)

interface Live {
  raw: ScreenPoint | null
  point: ScreenPoint | null
  tile: number | null
  candidate: number | null
  found: boolean
  hz: number
}

const readPerson = () => {
  try {
    return window.localStorage.getItem(PERSON_KEY) ?? ''
  } catch {
    return ''
  }
}

function usePortrait(): boolean {
  const q = '(orientation: portrait)'
  return useSyncExternalStore(
    (fn) => {
      const m = window.matchMedia(q)
      m.addEventListener('change', fn)
      return () => m.removeEventListener('change', fn)
    },
    () => window.matchMedia(q).matches,
  )
}

function download(name: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
  URL.revokeObjectURL(url)
}

const pct = (v: number) => `${Math.round(v * 100)}%`
const ms = (v: number | null) => (v === null ? '-' : `${Math.round(v)} ms`)

/**
 * /gaze-test: how we judge an eye tracker (docs/eye-tracking.md, "Gaze test"). A 2x3 grid laid out
 * like the board, a live gaze dot, the highlighted tile from the board's own TilePointer (filter,
 * sticky edges, hold), and a 10-target test: hit rate within 2 s and time to highlight, logged per
 * person in this browser. Sources: the gaze slot (the tablet shell or any tracker feeding it), the
 * head (MediaPipe, laptop only), or the mouse (to check the page itself).
 */
export default function GazeTestPage() {
  const bridge = nativeBridge()
  const portrait = usePortrait()
  const tuning = useSyncExternalStore(gazeTuning.subscribe, gazeTuning.get)
  const [person, setPerson] = useState(readPerson)
  const [source, setSource] = useState<TestSource>(nativeGazeActive() ? 'gaze' : 'mouse')
  const [margin, setMargin] = useState(STICKY_MARGIN)
  const [sdkFilter, setSdkFilter] = useState<boolean | null>(() => bridge?.gazeFilter() ?? null)
  const [live, setLive] = useState<Live>({ raw: null, point: null, tile: null, candidate: null, found: false, hz: 0 })
  const [run, setRun] = useState<TestRun | null>(null)
  const [, setTick] = useState(0)
  const [runs, setRuns] = useState<RunRecord[]>(load)
  const [panel, setPanel] = useState<'settings' | 'results' | null>(null)
  const [dwellP, setDwellP] = useState<{ tile: number; progress: number } | null>(null)
  const [flash, setFlash] = useState<number | null>(null)
  const [nativeMsg, setNativeMsg] = useState('')
  const [range, setRange] = useState<HeadRange>(DEFAULT_RANGE)
  const camera = useTrackerStatus()

  const pointer = useRef(new TilePointer(HEAD_TUNING))
  const dwellTimer = useRef(new DwellTimer())
  const runRef = useRef<TestRun | null>(null)
  const liveTile = useRef<number | null>(null) // the highlighted tile, null while not seen
  const blinks = useRef(0)
  const stamps = useRef<number[]>([])
  const latest = useRef({ source, tuning, margin, range })
  useEffect(() => {
    latest.current = { source, tuning, margin, range }
  })

  useEffect(() => {
    try {
      window.localStorage.setItem(PERSON_KEY, person)
    } catch {
      // not remembered; fine
    }
  }, [person])

  useEffect(() => {
    loadHeadRange().then((r) => r && setRange(r), () => {}) // no Core: the defaults
  }, [])

  // The head needs this page's camera; never while the tablet shell owns it.
  useEffect(() => {
    if (source !== 'head' || nativeGazeActive()) return
    void tracker.start()
    return () => tracker.stop()
  }, [source])

  // A new source starts the highlight over.
  useEffect(() => {
    pointer.current.reset(null)
    pointer.current.lost()
    dwellTimer.current.reset()
  }, [source])

  // Every sample of the chosen source -> the board's pipeline -> the live view and the run.
  useEffect(() => {
    const onSample = (s: PointSample, from: TestSource) => {
      const { source, tuning, margin } = latest.current
      if (from !== source) return
      const now = s.t
      stamps.current = [...stamps.current.filter((t) => now - t < 1000), now]
      const p = pointer.current
      p.tuning = from === 'head' ? { ...HEAD_TUNING, margin } : gazePointerTuning(tuning, margin)
      let point: ScreenPoint | null = null
      let candidate: number | null = null
      if (s.found && s.point) {
        const step = p.update(s.point, now, measureTiles(TILE_ATTR))
        point = step.point
        candidate = step.candidate
      } else {
        p.lost()
      }
      const tile = s.found ? p.tile : null
      liveTile.current = tile
      setLive({ raw: s.point, point, tile, candidate, found: s.found, hz: stamps.current.length })
      runRef.current?.update(now, tile)
      if (tuning.dwell) {
        dwellTimer.current.dwellMs = tuning.dwellMs
        const d = dwellTimer.current.update(tile, now)
        setDwellP(tile !== null && d.progress > 0 ? { tile, progress: d.progress } : null)
        if (d.fire && tile !== null) {
          setFlash(tile)
          window.setTimeout(() => setFlash(null), 400)
        }
      } else {
        setDwellP(null)
      }
    }
    const offGaze = gaze.subscribe((s) => onSample(s, 'gaze'))
    const offHead = tracker.subscribeSample((h) => onSample(fromHead(h, latest.current.range), 'head'))
    const onMove = (e: PointerEvent) =>
      onSample(
        { source: 'gaze', t: performance.now(), found: true, confidence: 1, point: { x: e.clientX / innerWidth, y: e.clientY / innerHeight } },
        'mouse',
      )
    const onLeave = () => onSample({ source: 'gaze', t: performance.now(), found: false, confidence: 0, point: null }, 'mouse')
    window.addEventListener('pointermove', onMove)
    document.documentElement.addEventListener('pointerleave', onLeave)
    // A gaze tracker that stops feeding: not seen, 0 Hz (instead of a frozen last sample).
    const watch = window.setInterval(() => {
      const now = performance.now()
      if (latest.current.source !== 'gaze' || gaze.connected(now)) return
      stamps.current = []
      onSample({ source: 'gaze', t: now, found: false, confidence: 0, point: null }, 'gaze')
      setLive((l) => ({ ...l, hz: 0 }))
    }, 200)
    return () => {
      window.clearInterval(watch)
      offGaze()
      offHead()
      window.removeEventListener('pointermove', onMove)
      document.documentElement.removeEventListener('pointerleave', onLeave)
    }
  }, [])

  // Blinks and calibration news from the tablet shell.
  useEffect(
    () =>
      nativeEvents.subscribe((e: NativeEvent) => {
        if (e.type === 'blink') {
          if (runRef.current && !runRef.current.done) blinks.current++
        } else if (e.type === 'calibration') {
          setNativeMsg(`calibration ${e.state.replace('_', ' ')}${e.person ? ` (${e.person})` : ''}`)
        } else if (e.type === 'tracker') {
          setNativeMsg(`tracker ${e.state}${e.detail ? `: ${e.detail}` : ''}`)
        }
      }),
    [],
  )

  // The run's clock: time-outs happen even when no samples arrive; finish and log when done.
  useEffect(() => {
    if (!run) return
    const id = window.setInterval(() => {
      const r = runRef.current
      if (!r) return
      if (!r.done) r.update(performance.now(), liveTile.current)
      setTick((n) => n + 1)
      if (r.done) {
        window.clearInterval(id)
        const { source, tuning, margin } = latest.current
        const record = makeRecord({
          person: person.trim() || 'unnamed',
          at: new Date().toISOString(),
          source,
          layout: portrait ? '2x3' : '3x2',
          tuning,
          margin,
          sdkFilter: nativeBridge()?.gazeFilter() ?? null,
          blinks: blinks.current,
          trials: r.trials,
        })
        setRuns((all) => {
          const next = [...all, record]
          save(next)
          return next
        })
        runRef.current = null
        setRun(null)
        setPanel('results')
      }
    }, TICK_MS)
    return () => window.clearInterval(id)
  }, [run, person, portrait])

  const start = () => {
    const r = new TestRun(makeTargets(TARGETS, 6, pointer.current.tile), performance.now())
    blinks.current = 0
    runRef.current = r
    setRun(r)
    setPanel(null)
  }
  const stop = () => {
    runRef.current = null
    setRun(null)
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') stop()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const setTuning = (t: Partial<GazeTuning>) => gazeTuning.set({ ...tuning, ...t })
  const target = run?.target ?? null
  const current = run ? summarize(run.trials) : null
  const last = runs.length ? runs[runs.length - 1] : null
  const bySource = (s: TestSource) => byPerson(runs.filter((r) => r.source === s))

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-black text-white">
      {/* Top bar, about the height of the board's breadcrumb so the tiles sit where the board's do. */}
      <div className="flex flex-wrap items-center gap-3 px-8 pt-4 text-base">
        <input
          value={person}
          onChange={(e) => setPerson(e.target.value)}
          placeholder="Person"
          className="w-36 rounded-lg bg-zinc-800 px-3 py-1 text-zinc-100 ring-1 ring-zinc-600"
        />
        <select
          value={source}
          onChange={(e) => setSource(e.target.value as TestSource)}
          disabled={run !== null}
          className="rounded-lg bg-zinc-800 px-2 py-1 ring-1 ring-zinc-600"
        >
          <option value="gaze">Gaze slot{nativeGazeActive() ? ' (tablet)' : ''}</option>
          <option value="head" disabled={nativeGazeActive()}>Head (webcam)</option>
          <option value="mouse">Mouse (check the page)</option>
        </select>
        {run ? (
          <button type="button" onClick={stop} className="rounded-lg bg-red-800 px-4 py-1 font-semibold">
            Stop (Esc)
          </button>
        ) : (
          <button
            type="button"
            onClick={start}
            disabled={!person.trim()}
            className="rounded-lg bg-sky-700 px-4 py-1 font-semibold disabled:opacity-40"
            title={person.trim() ? '' : 'Type a name first: results are logged per person'}
          >
            Start {TARGETS}-target test
          </button>
        )}
        {bridge && (
          <button
            type="button"
            onClick={() => bridge.calibrate(person.trim())}
            disabled={!person.trim() || run !== null}
            className="rounded-lg bg-zinc-700 px-3 py-1 disabled:opacity-40"
          >
            Calibrate {person.trim() || '...'}
          </button>
        )}
        <button type="button" onClick={() => setPanel(panel === 'settings' ? null : 'settings')} className="rounded-lg bg-zinc-800 px-3 py-1">
          Settings
        </button>
        <button type="button" onClick={() => setPanel(panel === 'results' ? null : 'results')} className="rounded-lg bg-zinc-800 px-3 py-1">
          Results ({runs.length})
        </button>
        <span className="ml-auto flex items-center gap-3 text-sm text-zinc-400">
          {run && current && (
            <span className="text-zinc-100">
              {run.trials.length}/{TARGETS} · {current.hits} hits
            </span>
          )}
          {!live.found && (
            <span className="rounded-full bg-zinc-800 px-3 py-0.5 font-semibold text-amber-200 ring-1 ring-amber-400/60">
              Eyes not detected
            </span>
          )}
          <span>
            {live.hz} Hz
            {source === 'gaze' && gaze.lastState ? ` · ${gaze.lastState}` : ''}
            {source === 'head' && camera.kind !== 'on' ? ` · camera ${camera.kind}` : ''}
            {nativeMsg ? ` · ${nativeMsg}` : ''}
            {` · ${portrait ? '2x3' : '3x2'}`}
          </span>
        </span>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-3 grid-rows-2 gap-6 px-8 pb-16 pt-8 portrait:grid-cols-2 portrait:grid-rows-3">
        {[0, 1, 2, 3, 4, 5].map((i) => {
          const on = i === live.tile
          const isTarget = i === target
          return (
            <div
              key={i}
              {...{ [TILE_ATTR]: i }}
              className={[
                'relative flex flex-col items-center justify-center rounded-3xl text-5xl font-bold transition-transform duration-150',
                on ? 'z-10 scale-105 bg-zinc-800 text-yellow-200 ring-[12px] ring-yellow-300' : 'bg-zinc-900 text-zinc-500 ring-2 ring-zinc-700',
                isTarget ? 'outline-dashed outline-8 outline-offset-4 outline-sky-400' : '',
                flash === i ? 'bg-yellow-900' : '',
              ].join(' ')}
            >
              <span>{i + 1}</span>
              {isTarget && <span className="mt-2 text-3xl text-sky-300">Look here</span>}
              {dwellP?.tile === i && (
                <span className="absolute bottom-4 h-2 w-1/2 overflow-hidden rounded-full bg-zinc-700">
                  <span className="block h-full bg-yellow-300" style={{ width: pct(dwellP.progress) }} />
                </span>
              )}
            </div>
          )
        })}
      </div>

      {live.raw && (
        <div
          aria-hidden
          className="pointer-events-none fixed z-30 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-zinc-400/60"
          style={{ left: pct(live.raw.x), top: pct(live.raw.y) }}
        />
      )}
      {live.point && (
        <div
          aria-hidden
          className="pointer-events-none fixed z-30 h-8 w-8 -translate-x-1/2 -translate-y-1/2 rounded-full bg-sky-300/50 ring-2 ring-sky-200/80"
          style={{ left: pct(live.point.x), top: pct(live.point.y) }}
        />
      )}

      {panel === 'settings' && (
        <div className="fixed right-4 top-16 z-40 w-80 space-y-3 rounded-2xl bg-zinc-900/95 p-4 text-sm ring-1 ring-zinc-600">
          <p className="text-xs text-zinc-400">Saved in this browser; the board uses the same gaze settings.</p>
          <Toggle label="One Euro filter" on={tuning.oneEuro} set={(v) => setTuning({ oneEuro: v })} />
          <Slider label="min cutoff (Hz)" value={tuning.euro.minCutoff} range={LIMITS.minCutoff} step={0.05}
            set={(v) => setTuning({ euro: { ...tuning.euro, minCutoff: v } })} />
          <Slider label="beta" value={tuning.euro.beta} range={LIMITS.beta} step={0.5}
            set={(v) => setTuning({ euro: { ...tuning.euro, beta: v } })} />
          <Toggle
            label={sdkFilter === null ? 'SDK gaze filter (tablet only)' : 'SDK gaze filter (Eyedid)'}
            on={sdkFilter ?? false}
            disabled={sdkFilter === null}
            set={(v) => {
              bridge?.setGazeFilter(v)
              setSdkFilter(bridge?.gazeFilter() ?? null)
            }}
          />
          <Slider label="hold before switching (ms)" value={tuning.holdMs} range={LIMITS.holdMs} step={25} set={(v) => setTuning({ holdMs: v })} />
          <Slider label="sticky margin (%)" value={Math.round(margin * 100)} range={[0, MAX_STICKY_MARGIN * 100]} step={1}
            set={(v) => setMargin(v / 100)} />
          <Toggle label="Dwell ring (shows only here)" on={tuning.dwell} set={(v) => setTuning({ dwell: v })} />
          <Slider label="dwell (ms)" value={tuning.dwellMs} range={LIMITS.dwellMs} step={100} set={(v) => setTuning({ dwellMs: v })} />
          <button type="button" className="rounded-lg bg-zinc-700 px-3 py-1" onClick={() => gazeTuning.set(DEFAULT_GAZE_TUNING)}>
            Defaults
          </button>
          <p className="text-xs text-zinc-500">
            Head uses the board's head settings (no filter or hold here). Sticky margin is for this page only; the
            board's comes from the Core.
          </p>
        </div>
      )}

      {panel === 'results' && (
        <div className="fixed inset-x-8 top-16 z-40 max-h-[80vh] overflow-auto rounded-2xl bg-zinc-900/95 p-5 text-sm ring-1 ring-zinc-600">
          {last && (
            <p className="mb-4 text-lg">
              Last run ({last.person}, {last.source}): <b>{pct(last.summary.hitRate)}</b> hit rate,{' '}
              {ms(last.summary.avgMs)} average to highlight, {last.summary.wrong} wrong highlights, {last.blinks} blinks{' '}
              <span className={last.summary.pass ? 'text-emerald-300' : 'text-red-300'}>
                {last.summary.pass ? 'PASS (>= 90%)' : 'below 90%'}
              </span>
            </p>
          )}
          {(['gaze', 'head', 'mouse'] as const).map((s) => {
            const rows = bySource(s)
            if (!rows.length) return null
            return (
              <table key={s} className="mb-4 w-full text-left">
                <caption className="mb-1 text-left text-zinc-400">
                  {s} · a person passes with a run at 90% or better (hits within {TIMEOUT_MS / 1000} s)
                </caption>
                <thead className="text-zinc-400">
                  <tr><th>Person</th><th>Runs</th><th>Best run</th><th>All trials</th><th>Avg to highlight</th></tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.person}>
                      <td>{r.person}</td><td>{r.runs}</td>
                      <td className={r.best >= 0.9 ? 'text-emerald-300' : 'text-red-300'}>{pct(r.best)}</td>
                      <td>{pct(r.hitRate)}</td><td>{ms(r.avgMs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          })}
          <div className="flex gap-3">
            <button type="button" className="rounded-lg bg-zinc-700 px-3 py-1" disabled={!runs.length}
              onClick={() => download('gaze-test.json', JSON.stringify(runs, null, 2), 'application/json')}>
              Export JSON
            </button>
            <button type="button" className="rounded-lg bg-zinc-700 px-3 py-1" disabled={!runs.length}
              onClick={() => download('gaze-test.csv', toCsv(runs), 'text/csv')}>
              Export CSV
            </button>
            <button type="button" className="ml-auto rounded-lg bg-red-900 px-3 py-1" disabled={!runs.length}
              onClick={() => {
                setRuns([])
                save([])
              }}>
              Clear log
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

function Toggle({ label, on, set, disabled }: { label: string; on: boolean; set: (v: boolean) => void; disabled?: boolean }) {
  return (
    <label className={`flex items-center justify-between ${disabled ? 'opacity-40' : ''}`}>
      <span>{label}</span>
      <input type="checkbox" checked={on} disabled={disabled} onChange={(e) => set(e.target.checked)} className="h-5 w-5 accent-yellow-300" />
    </label>
  )
}

function Slider({ label, value, range, step, set }: {
  label: string
  value: number
  range: readonly [number, number]
  step: number
  set: (v: number) => void
}) {
  return (
    <label className="block">
      <span className="flex justify-between text-zinc-300"><span>{label}</span><span>{value}</span></span>
      <input type="range" min={range[0]} max={range[1]} step={step} value={value} onChange={(e) => set(Number(e.target.value))}
        className="w-full accent-yellow-300" />
    </label>
  )
}
