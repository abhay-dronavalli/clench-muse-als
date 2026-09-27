import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useGazeOver } from '../facetrack/gazeOver'
import type { Confirm, Lang, Screen, Tile } from '../contracts'
import { STRINGS } from './strings'
import { LAUNCH_MS, SetupCountdown } from './onboardingFlow'

export function Breadcrumb({ screen }: { screen: Screen }) {
  const parts = [STRINGS[screen.lang].home, ...screen.path]
  return (
    <nav className="px-8 pt-6 text-3xl font-semibold text-zinc-400" aria-label="breadcrumb">
      {parts.map((p, i) => (
        <span key={i}>
          {i > 0 && <span className="px-3 text-zinc-600">›</span>}
          <span className={i === parts.length - 1 ? 'text-white' : undefined}>{p}</span>
        </span>
      ))}
    </nav>
  )
}

/** Circular arrows: "Other..." brings new options. */
function RefreshIcon() {
  return (
    <svg viewBox="0 0 24 24" className="h-[0.9em] w-[0.9em] shrink-0" fill="none" stroke="currentColor"
      strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M20 11a8 8 0 0 0-14.3-4.9L4 8" />
      <path d="M4 3v5h5" />
      <path d="M4 13a8 8 0 0 0 14.3 4.9L20 16" />
      <path d="M20 21v-5h-5" />
    </svg>
  )
}

const TEXT: Record<Tile['kind'], string> = {
  branch: 'text-5xl leading-tight xl:text-6xl',
  leaf: 'text-5xl leading-tight xl:text-6xl',
  answer: 'text-5xl leading-tight xl:text-6xl', // a Support question's answer (core/car)
  corner: 'text-3xl leading-tight', // drawn by BoardPage's CornerButton, not in the grid
  // A whole sentence: smaller so it wraps onto a few lines and is never cut off.
  suggestion: 'text-3xl leading-snug xl:text-4xl',
  other: 'text-5xl leading-tight xl:text-6xl',
  car: 'text-5xl leading-tight xl:text-6xl', // trip controls draw in trip.tsx; here only for completeness
  back: 'text-5xl leading-tight xl:text-6xl',
}

function tileLook(tile: Tile, on: boolean): string {
  if (tile.kind === 'other') {
    // Outlined, no fill: clearly not one of the options themselves.
    return on
      ? 'relative z-10 scale-105 border-4 border-dashed border-yellow-300 bg-transparent text-yellow-200 ring-[12px] ring-yellow-300'
      : 'border-4 border-dashed border-zinc-500 bg-transparent text-zinc-300'
  }
  return on
    ? 'relative z-10 scale-105 bg-zinc-800 text-yellow-200 ring-[12px] ring-yellow-300'
    : 'bg-zinc-900 text-zinc-100 ring-2 ring-zinc-700'
}

/**
 * Up to 6 huge tiles in a fixed 3x2 grid (2x3 on a portrait screen, e.g. the tablet held upright),
 * so a tile's position never depends on how many there are. The deeper bottom padding keeps the
 * collapsed dev panel pill clear of the highlighted tile's ring.
 *
 * While the Core is loading AI options (`screen.loading`) the picked tile pulses gently and a
 * "Finding options…" line shows; the Core has paused the scan.
 */
export function TileGrid({ screen, onTap }: { screen: Screen; onTap?: (tile: number) => void }) {
  const loading = screen.loading === true
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div className="grid min-h-0 flex-1 grid-cols-3 grid-rows-2 gap-6 px-8 pb-16 pt-8 portrait:grid-cols-2 portrait:grid-rows-3">
        {screen.tiles.map((tile, i) => {
          const on = i === screen.highlight
          return (
            <div
              key={tile.id}
              data-tile-index={i} // webcam pointing measures the tiles on screen (facetrack/)
              aria-current={on}
              aria-busy={on && loading}
              // A touch or click picks this tile (TAP), for a caregiver or testing without a headband.
              onClick={onTap && !loading ? () => onTap(i) : undefined}
              className={[
                onTap && !loading ? 'cursor-pointer select-none' : '',
                'flex items-center justify-center gap-4 rounded-3xl p-6 text-center font-bold',
                'break-words transition-transform duration-150',
                TEXT[tile.kind],
                tileLook(tile, on),
                on && loading ? 'animate-pulse' : '',
              ].join(' ')}
            >
              {tile.kind === 'other' && <RefreshIcon />}
              <span>{tile.label}</span>
            </div>
          )
        })}
      </div>
      {loading && (
        <div role="status" className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
          <span className="flex items-center gap-4 rounded-full bg-zinc-800/95 px-8 py-3 text-3xl font-semibold text-yellow-200 ring-2 ring-yellow-300/60">
            <span className="flex gap-2" aria-hidden>
              {[0, 200, 400].map((delay) => (
                <span key={delay} className="h-3 w-3 animate-pulse rounded-full bg-yellow-300"
                  style={{ animationDelay: `${delay}ms` }} />
              ))}
            </span>
            {STRINGS[screen.lang].finding}
          </span>
        </div>
      )}
    </div>
  )
}

export function ConfirmView({ confirm, lang, onTap }: { confirm: Confirm; lang: Lang; onTap?: () => void }) {
  const s = STRINGS[lang]
  const [confirmRef, gazeOn] = useGazeOver<HTMLParagraphElement>() // gaze lights the sentence (a clench confirms it)
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-12 p-12 text-center">
      <p className="text-4xl font-semibold text-yellow-300">{s.confirm[confirm.action]}</p>
      <p
        ref={confirmRef}
        aria-current={gazeOn}
        onClick={onTap} // a touch or click on the sentence confirms it (TAP), like a clench
        className={`max-w-6xl rounded-3xl p-10 text-6xl font-bold leading-tight ring-[12px] ring-yellow-300 transition-transform xl:text-7xl ${gazeOn ? 'scale-105 bg-yellow-300/20' : ''} ${onTap ? 'cursor-pointer select-none' : ''}`}
      >
        {confirm.text}
      </p>
      <p className="whitespace-pre text-3xl text-zinc-300">{s.hint}</p>
    </div>
  )
}

export function SpeakingView({ text, lang }: { text: string; lang: Lang }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-12 p-12 text-center">
      <p className="flex items-center gap-4 text-4xl font-semibold text-emerald-300">
        <span className="flex h-10 items-end gap-1.5" aria-hidden>
          {[0, 150, 300, 450].map((delay) => (
            <span
              key={delay}
              className="w-2.5 animate-pulse rounded bg-emerald-300"
              style={{ height: '100%', animationDelay: `${delay}ms` }}
            />
          ))}
        </span>
        {STRINGS[lang].speaking}
      </p>
      <p className="max-w-6xl text-6xl font-bold leading-tight xl:text-7xl">{text}</p>
    </div>
  )
}

/** Full-screen red help countdown (PRD D3): a huge number and how to cancel. */
/**
 * "Go back?" over the board after a double blink (BACK_PROMPT). A clench confirms; doing nothing lets
 * the bar run out and nothing changes. The bar shows how long is left.
 */
export function BackPromptView({ kind, ms, lang, onStay }: { kind: 'menu' | 'confirm'; ms: number; lang: Lang; onStay?: () => void }) {
  const s = STRINGS[lang].back
  const [full, setFull] = useState(true)
  useEffect(() => {
    const frame = requestAnimationFrame(() => setFull(false)) // start the bar shrinking next frame
    return () => cancelAnimationFrame(frame)
  }, [])
  return (
    <div role="alertdialog" aria-live="assertive"
      className="pointer-events-none fixed inset-0 z-30 flex items-center justify-center bg-black/60">
      <div className="w-[min(90vw,48rem)] rounded-3xl bg-zinc-900 p-10 text-center text-white shadow-2xl ring-4 ring-sky-400">
        <p className="text-7xl font-bold">↩ {s[kind]}</p>
        <p className="mt-6 text-4xl font-semibold text-sky-300">{s.how}</p>
        <p className="mt-2 text-2xl text-zinc-400">{s.stay[kind]}</p>
        <div className="mt-8 h-4 overflow-hidden rounded-full bg-zinc-700">
          <div className="h-4 rounded-full bg-sky-400"
            style={{ width: full ? '100%' : '0%', transition: full ? 'none' : `width ${ms}ms linear` }} />
        </div>
        {onStay && (
          // A touch closes the prompt and stays (nothing depends on blinks: the tablet has none yet).
          <button type="button" onClick={onStay}
            className="pointer-events-auto mt-8 w-full rounded-3xl bg-white px-10 py-8 text-5xl font-bold text-zinc-900">
            {lang === 'es' ? 'Quedarme aquí' : 'Stay here'}
          </button>
        )}
      </div>
    </div>
  )
}

export function HelpCountdownView({ countdown, lang, onCancel }: { countdown: number; lang: Lang; onCancel?: () => void }) {
  const s = STRINGS[lang].help
  return (
    <div
      role="alert"
      className="fixed inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-red-700 p-12 text-center text-white"
    >
      <p className="text-6xl font-bold">{s.title}</p>
      <p className="text-[18rem] font-black leading-none tabular-nums">{countdown}</p>
      <p className="text-5xl font-semibold">{s.cancel}</p>
      {onCancel && (
        // A large touch Cancel: stopping a false alarm must not depend on blinks (the tablet has none yet).
        <button type="button" onClick={onCancel}
          className="mt-4 min-w-[28rem] rounded-3xl bg-white px-16 py-10 text-7xl font-black text-red-700 shadow-2xl">
          {lang === 'es' ? 'Cancelar' : 'Cancel'}
        </button>
      )}
    </div>
  )
}

export function StartOverlay({ onStart, lang }: { onStart: (gesture?: boolean) => void; lang: Lang }) {
  const t = STRINGS[lang].start
  const [left, setLeft] = useState(LAUNCH_MS / 1000)
  const startRef = useRef(onStart)
  useLayoutEffect(() => { startRef.current = onStart })
  useEffect(() => {
    const countdown = new SetupCountdown(LAUNCH_MS, performance.now())
    const timer = window.setInterval(() => {
      const remaining = countdown.tick(performance.now(), document.hidden)
      setLeft(Math.max(0, Math.ceil(remaining / 1000)))
      if (remaining <= 0) { window.clearInterval(timer); startRef.current(false) }
    }, 100)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center overflow-y-auto bg-black p-6 text-amber-50">
      <div className="flex w-full max-w-3xl flex-col items-center gap-8 rounded-[2.5rem] bg-zinc-900 px-8 py-12 text-center shadow-2xl shadow-black/60 sm:px-16 sm:py-16">
        <h1 className="text-6xl font-bold tracking-tight text-[#d4a017] sm:text-8xl">Clench</h1>
        <button
          type="button"
          onClick={() => onStart(true)}
          className="w-full rounded-3xl bg-[#d4a017] px-8 py-6 text-3xl font-bold text-white shadow-lg shadow-[#d4a017]/30 transition-colors hover:bg-[#b8890f] focus-visible:outline-4 focus-visible:outline-offset-4 focus-visible:outline-[#d4a017] active:bg-[#9c740c] sm:text-5xl"
        >
          {t.button}
        </button>
        <p className="text-2xl text-zinc-300">{t.auto(left)}</p>
        <div role="progressbar" aria-label={t.auto(left)} aria-valuemin={0} aria-valuemax={LAUNCH_MS / 1000} aria-valuenow={LAUNCH_MS / 1000 - left} className="h-3 w-full overflow-hidden rounded-full bg-zinc-700">
          <div className="h-full bg-[#d4a017] transition-[width]" style={{ width: `${(1 - left / (LAUNCH_MS / 1000)) * 100}%` }} />
        </div>
        <p className="text-lg text-zinc-400">{t.hint}</p>
      </div>
    </div>
  )
}
