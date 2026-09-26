import type { Confirm, Lang, Screen } from '../contracts'
import { STRINGS } from './strings'

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

/**
 * Up to 6 huge tiles in a fixed 3x2 grid, so a tile's position never depends on how many there are.
 * The deeper bottom padding keeps the collapsed dev panel pill clear of the highlighted tile's ring.
 */
export function TileGrid({ screen }: { screen: Screen }) {
  return (
    <div className="grid min-h-0 flex-1 grid-cols-3 grid-rows-2 gap-6 px-8 pb-16 pt-8">
      {screen.tiles.map((tile, i) => {
        const on = i === screen.highlight
        return (
          <div
            key={tile.id}
            aria-current={on}
            className={[
              'flex items-center justify-center rounded-3xl p-6 text-center font-bold leading-tight',
              'text-5xl break-words transition-transform duration-150 xl:text-6xl',
              on
                ? 'relative z-10 scale-105 bg-zinc-800 text-yellow-200 ring-[12px] ring-yellow-300'
                : 'bg-zinc-900 text-zinc-100 ring-2 ring-zinc-700',
            ].join(' ')}
          >
            {tile.label}
          </div>
        )
      })}
    </div>
  )
}

export function ConfirmView({ confirm, lang }: { confirm: Confirm; lang: Lang }) {
  const s = STRINGS[lang]
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-12 p-12 text-center">
      <p className="text-4xl font-semibold text-yellow-300">{s.confirm[confirm.action]}</p>
      <p className="max-w-6xl rounded-3xl p-10 text-6xl font-bold leading-tight ring-[12px] ring-yellow-300 xl:text-7xl">
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
export function HelpCountdownView({ countdown, lang }: { countdown: number; lang: Lang }) {
  const s = STRINGS[lang].help
  return (
    <div
      role="alert"
      className="fixed inset-0 z-20 flex flex-col items-center justify-center gap-6 bg-red-700 p-12 text-center text-white"
    >
      <p className="text-6xl font-bold">{s.title}</p>
      <p className="text-[18rem] font-black leading-none tabular-nums">{countdown}</p>
      <p className="text-5xl font-semibold">{s.cancel}</p>
    </div>
  )
}

export function StartOverlay({ onStart }: { onStart: () => void }) {
  return (
    <button
      type="button"
      onClick={onStart}
      className="fixed inset-0 z-40 flex cursor-pointer flex-col items-center justify-center gap-8 bg-black text-white"
    >
      <span className="text-8xl font-bold tracking-tight">Clench</span>
      <span className="rounded-3xl px-12 py-6 text-5xl font-semibold ring-8 ring-yellow-300">Click to start</span>
      <span className="text-2xl text-zinc-400">Turns on speech. Haga clic para empezar.</span>
    </button>
  )
}
