import type { ReactNode } from 'react'
import type { CarState, Lang, Screen, WindowsOpen } from '../contracts'
import type { CarAnim } from './carAnimation'
import { STRINGS } from './strings'

/**
 * The trip screen (SCREEN `screen: "trip"`, core/trip.py): a telemetry strip (CAR_STATE), the 3D car
 * in the upper part, and the trip menu's tiles in the middle of the screen (gaze is least accurate at
 * the edges) on a translucent panel that rises from the bottom to just below the car. The car and its
 * world are drawn by the tablet shell behind this page (SceneView, see native.ts); in a normal browser
 * a soft sky-to-grass backdrop takes their place.
 *
 * A control that acts (CAR_ACTION) plays one calm sequence while the Core locks input for `ms`: the
 * other tiles fade out, the picked one stays and grows a little (the rider's eyes keep an anchor), then
 * everything fades back. Pull over is heavier and plainer: a warm copper tint, no scale, the drive
 * eases to a stop. No flashing anywhere: every change is a slow opacity or scale transition.
 */

const COPPER = 'rgba(192, 90, 44, 0.16)' // the pull-over tint: warm rust, low contrast
// In a browser (no 3D scene behind the page): a soft sky-to-grass backdrop in its place.
const BROWSER_BACKDROP = 'bg-[linear-gradient(to_bottom,#b9d9f4_0%,#e4f1fb_30%,#d3ebc8_45%,#a4d18f_100%)]'
const WINDOW_ORDER: (keyof WindowsOpen)[] = ['front_left', 'front_right', 'rear_left', 'rear_right']

function Telemetry({ lang, car }: { lang: Lang; car: CarState | null }) {
  const s = STRINGS[lang].trip
  const item = (label: string, value: ReactNode) => (
    <span className="flex items-baseline gap-2 whitespace-nowrap">
      <span className="text-zinc-500">{label}</span>
      <span className="font-semibold tabular-nums text-zinc-900">{value}</span>
    </span>
  )
  const dash = '–'
  return (
    <div className="flex shrink-0 flex-wrap items-center justify-center gap-x-10 gap-y-1 border-b border-black/5 bg-white/85 px-6 py-2 text-xl shadow-sm">
      {item(s.speed, car ? `${car.speed_mph} ${s.mph}` : dash)}
      {item(s.eta, car ? `${car.eta_min} ${s.min}` : dash)}
      {item(s.battery, car ? `${car.battery_pct}%` : dash)}
      {item(s.temp, car ? `${car.cabin_temp_f}°F` : dash)}
      {item(
        s.windows,
        <span className="flex gap-3">
          {WINDOW_ORDER.map((w) => (
            <span key={w} className="flex items-baseline gap-1">
              <span className="text-base font-normal text-zinc-500">{s.windowShort[w]}</span>
              {car ? `${car.windows[w]}%` : dash}
            </span>
          ))}
        </span>,
      )}
      {item(s.volume, car ? `${car.volume}/10` : dash)}
    </div>
  )
}

/** The upper part: empty (the tablet draws the car behind it), or a placeholder label in a browser. */
function CarArea({ nativeCar, lang }: { nativeCar: boolean; lang: Lang }) {
  return (
    <div className="relative flex min-h-0 flex-[4] items-center justify-center">
      {!nativeCar && (
        <span className="rounded-full bg-white/70 px-4 py-1 text-lg text-zinc-600">{STRINGS[lang].trip.carHere}</span>
      )}
    </div>
  )
}

/**
 * Telemetry, car area, and the panel below with whatever goes on it (the tiles, a confirm screen, a
 * spoken line). The panel is translucent white from the bottom of the screen to just below the car.
 */
function TripShell({
  lang,
  car,
  nativeCar,
  tint,
  children,
}: {
  lang: Lang
  car: CarState | null
  nativeCar: boolean
  tint: boolean
  children: ReactNode
}) {
  return (
    <div className={`relative flex min-h-0 flex-1 flex-col ${nativeCar ? '' : BROWSER_BACKDROP}`}>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-20 transition-opacity duration-700 ease-out"
        style={{ background: COPPER, opacity: tint ? 1 : 0 }}
      />
      <Telemetry lang={lang} car={car} />
      <CarArea nativeCar={nativeCar} lang={lang} />
      <div className="relative z-10 flex min-h-0 flex-[6] flex-col items-center justify-center px-8 pb-8 pt-4">
        {/* Starts a little above this section, right below the car, and runs to the bottom edge. */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-x-0 -top-[4vh] bottom-0 rounded-t-[3rem] border-t border-white/70 bg-white/45"
        />
        <div className="relative flex h-full w-full flex-col items-center justify-center">{children}</div>
      </div>
    </div>
  )
}

/** The tile a CAR_ACTION belongs to: its window (window controls) or its own name. */
function pickedTile(screen: Screen, anim: CarAnim | null): number {
  if (!anim) return -1
  const key = anim.window ?? anim.action
  return screen.tiles.findIndex((t) => t.id.endsWith(`.${key}`))
}

export function TripView({
  screen,
  car,
  anim,
  phase,
  tint,
  nativeCar,
  onTap,
}: {
  screen: Screen
  car: CarState | null
  anim: CarAnim | null
  phase: 'in' | 'out'
  tint: boolean
  nativeCar: boolean
  onTap: (tile: number) => void
}) {
  const picked = pickedTile(screen, anim)
  const locked = anim !== null
  return (
    <TripShell lang={screen.lang} car={car} nativeCar={nativeCar} tint={tint}>
      {screen.path.length > 0 && (
        <p className="mb-4 text-2xl font-semibold text-zinc-700">{screen.path.join('  ›  ')}</p>
      )}
      <div className="grid w-full max-w-6xl flex-1 auto-rows-fr grid-cols-3 gap-8 portrait:grid-cols-2">
        {screen.tiles.map((tile, i) => {
          const on = !locked && i === screen.highlight
          const isPicked = i === picked
          const hidden = locked && phase === 'out' && !isPicked
          const grow = isPicked && phase === 'out' && anim?.action !== 'pull_over'
          const safety = tile.id.endsWith('.pull_over')
          const back = tile.kind === 'back'
          return (
            <div
              key={tile.id}
              data-tile-index={i} // pointing measures the tiles on screen (facetrack/)
              aria-current={on}
              onClick={locked ? undefined : () => onTap(i)}
              className={[
                'flex select-none items-center justify-center rounded-3xl px-6 text-center',
                'text-4xl font-bold leading-tight xl:text-5xl', // >= 36 px, sentence case from the Core
                'transition-[opacity,transform,box-shadow] duration-[350ms] ease-out',
                locked ? '' : 'cursor-pointer',
                hidden ? 'opacity-0' : 'opacity-100',
                grow ? 'scale-[1.06]' : on ? 'scale-105' : 'scale-100',
                on || isPicked
                  ? safety
                    ? 'bg-[#fff4ee] text-[#8a3510] shadow-2xl shadow-black/15 ring-[10px] ring-[#c05a2c]'
                    : 'bg-white text-zinc-900 shadow-2xl shadow-black/15 ring-[10px] ring-amber-500'
                  : safety
                    ? 'bg-white text-[#9a3f16] shadow-xl shadow-black/10 ring-2 ring-[#d58a62]'
                    : back
                      ? 'bg-zinc-100 text-zinc-600 shadow-md shadow-black/5 ring-1 ring-black/10'
                      : 'bg-white text-zinc-900 shadow-xl shadow-black/10 ring-1 ring-black/10',
              ].join(' ')}
            >
              {back ? `‹ ${tile.label}` : tile.label}
            </div>
          )
        })}
      </div>
    </TripShell>
  )
}

/**
 * The trip's confirm screen: Pull over (plain, heavy, copper) or Support (a call). A clench confirms,
 * a double blink cancels; Confirm and Cancel are also big targets for a tap.
 */
export function TripConfirm({
  action,
  lang,
  car,
  nativeCar,
  onConfirm,
  onCancel,
}: {
  action: 'pull_over' | 'support'
  lang: Lang
  car: CarState | null
  nativeCar: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const s = STRINGS[lang].trip
  const pull = action === 'pull_over'
  return (
    <TripShell lang={lang} car={car} nativeCar={nativeCar} tint={pull}>
      <div className="flex flex-col items-center rounded-[2.5rem] bg-white/95 px-16 py-12 shadow-2xl shadow-black/15">
        <p className={`mb-10 text-6xl font-bold ${pull ? 'text-[#7a2e0e]' : 'text-zinc-900'}`}>
          {pull ? s.pullOver : s.support}
        </p>
        <div className="flex gap-10">
          <button
            type="button"
            onClick={onConfirm}
            className={`min-w-72 rounded-3xl px-12 py-10 text-5xl font-bold text-white shadow-lg ${pull ? 'bg-[#c05a2c]' : 'bg-sky-700'}`}
          >
            {s.confirm}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="min-w-72 rounded-3xl bg-white px-12 py-10 text-5xl font-bold text-zinc-900 ring-2 ring-zinc-300"
          >
            {s.cancel}
          </button>
        </div>
        <p className="mt-10 text-2xl text-zinc-500">{s.how}</p>
      </div>
    </TripShell>
  )
}

/** A spoken line during the trip (a confirmed sentence): same layout, the car stays in view. */
export function TripSpeaking({
  lang,
  text,
  car,
  nativeCar,
  tint,
}: {
  lang: Lang
  text: string
  car: CarState | null
  nativeCar: boolean
  tint: boolean
}) {
  return (
    <TripShell lang={lang} car={car} nativeCar={nativeCar} tint={tint}>
      <p className="max-w-5xl rounded-[2.5rem] bg-white/95 px-14 py-10 text-center text-5xl font-bold leading-tight text-zinc-900 shadow-2xl shadow-black/15">
        {text}
      </p>
    </TripShell>
  )
}
