import type { ReactNode } from 'react'
import type { CarState, Lang, Screen, TripLayout, WindowsOpen } from '../contracts'
import type { CarAnim } from './carAnimation'
import { RouteMap } from './RouteMap'
import { STRINGS } from './strings'

/**
 * The trip screen (SCREEN `screen: "trip"`, core/trip.py). A telemetry column on the left (CAR_STATE),
 * and beside it, by layout (SETTINGS `trip_layout`, the Car / Split / Map switch at the column's foot):
 *
 *   car    the 3D car above, the trip menu's tiles below
 *   split  the route map on the left half; the car above three tiles on the right half (the Core
 *          sends only Windows, Pull over and Support at the top level here)
 *   map    the route map above, the tiles below
 *
 * The tiles sit on a dark translucent panel that rises from the bottom to just below the car. The car
 * and its world are drawn by the tablet shell behind this page (SceneView, see native.ts); in a normal
 * browser a soft sky-to-grass backdrop takes their place.
 *
 * A control that acts (CAR_ACTION) plays one calm sequence while the Core locks input for `ms`: the
 * other tiles fade out, the picked one stays and grows a little (the rider's eyes keep an anchor), then
 * everything fades back. Pull over is heavier and plainer: a warm copper tint, no scale, the drive
 * eases to a stop. No flashing anywhere: every change is a slow opacity or scale transition.
 */

// The layout switch offers Car and Split (Map is off the switch; the Core still accepts it).
const TRIP_LAYOUTS: TripLayout[] = ['car', 'split']
const COPPER = 'rgba(192, 90, 44, 0.16)' // the pull-over tint: warm rust, low contrast
// In a browser (no 3D scene behind the page): a soft sky-to-grass backdrop in its place.
const BROWSER_BACKDROP = 'bg-[linear-gradient(to_bottom,#b9d9f4_0%,#e4f1fb_30%,#d3ebc8_45%,#a4d18f_100%)]'
const WINDOW_ORDER: (keyof WindowsOpen)[] = ['front_left', 'front_right', 'rear_left', 'rear_right']

/** The ride's numbers, top to bottom, and the layout switch at the foot. */
function Telemetry({
  lang,
  car,
  layout,
  onLayout,
}: {
  lang: Lang
  car: CarState | null
  layout: TripLayout
  onLayout: (l: TripLayout) => void
}) {
  const s = STRINGS[lang].trip
  const dash = '–'
  const item = (label: string, value: ReactNode) => (
    <div className="flex flex-col">
      <span className="text-lg text-zinc-500">{label}</span>
      <span className="text-3xl font-semibold tabular-nums text-zinc-900">{value}</span>
    </div>
  )
  return (
    <aside className="relative z-30 flex min-h-0 w-72 shrink-0 flex-col gap-6 border-r border-black/5 bg-white/85 px-6 py-6 shadow-md">
      <div className="flex min-h-0 flex-1 flex-col gap-6 overflow-y-auto">
        {item(s.speed, car ? `${car.speed_mph} ${s.mph}` : dash)}
        {item(s.eta, car ? `${car.eta_min} ${s.min}` : dash)}
        {item(s.battery, car ? `${car.battery_pct}%` : dash)}
        {item(s.temp, car ? `${car.cabin_temp_f}°F` : dash)}
        <div className="flex flex-col">
          <span className="text-lg text-zinc-500">{s.windows}</span>
          <div className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1">
            {WINDOW_ORDER.map((w) => (
              <span key={w} className="flex items-baseline gap-2">
                <span className="text-base text-zinc-500">{s.windowShort[w]}</span>
                <span className="text-2xl font-semibold tabular-nums text-zinc-900">{car ? `${car.windows[w]}%` : dash}</span>
              </span>
            ))}
          </div>
        </div>
        {item(s.volume, car ? `${car.volume}/10` : dash)}
      </div>
      <div className="flex shrink-0 flex-col gap-4">
        {TRIP_LAYOUTS.map((l) => (
          <button
            key={l}
            type="button"
            onClick={() => onLayout(l)}
            aria-pressed={l === layout}
            className={`min-h-32 rounded-3xl px-4 py-6 text-5xl font-bold leading-tight transition-colors focus-visible:outline-4 focus-visible:outline-offset-2 focus-visible:outline-[#007a72] ${l === layout ? 'bg-zinc-900 text-white ring-8 ring-amber-500' : 'bg-zinc-100 text-zinc-800 ring-2 ring-black/15 hover:bg-zinc-200'}`}
          >
            {l === layout ? `✓ ${s.layout[l]}` : s.layout[l]}
          </button>
        ))}
      </div>
    </aside>
  )
}

/** Where the car shows: empty here (the tablet draws it behind the page), or a label in a browser. */
function CarSpot({ nativeCar, lang }: { nativeCar: boolean; lang: Lang }) {
  return (
    <div className="relative flex min-h-0 flex-[4] items-center justify-center">
      {!nativeCar && <span className="rounded-full bg-white/70 px-4 py-1 text-lg text-zinc-600">{STRINGS[lang].trip.carHere}</span>}
    </div>
  )
}

/** The tiles' panel: dark smoked glass from just below the car (or the map) to the bottom edge. */
function Panel({ children }: { children: ReactNode }) {
  return (
    <div className="relative z-10 flex min-h-0 flex-[6] flex-col items-center justify-center px-8 pb-8 pt-4">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 -top-[4vh] bottom-0 rounded-t-[3rem] border-t border-white/15 bg-zinc-950/45"
      />
      <div className="relative flex h-full w-full flex-col items-center justify-center">{children}</div>
    </div>
  )
}

function TripShell({
  lang,
  car,
  nativeCar,
  tint,
  layout,
  onLayout,
  children,
}: {
  lang: Lang
  car: CarState | null
  nativeCar: boolean
  tint: boolean
  layout: TripLayout
  onLayout: (l: TripLayout) => void
  children: ReactNode
}) {
  let main: ReactNode
  if (layout === 'split') {
    main = (
      <div className="flex min-h-0 flex-1">
        <div className="relative z-10 min-h-0 flex-1 p-6">
          <RouteMap car={car} lang={lang} />
        </div>
        <div className="flex min-h-0 flex-1 flex-col">
          <CarSpot nativeCar={nativeCar} lang={lang} />
          <Panel>{children}</Panel>
        </div>
      </div>
    )
  } else {
    main = (
      <div className="flex min-h-0 flex-1 flex-col">
        {layout === 'map' ? (
          <div className="relative z-10 min-h-0 flex-[4] px-8 pb-[5vh] pt-6">
            <RouteMap car={car} lang={lang} />
          </div>
        ) : (
          <CarSpot nativeCar={nativeCar} lang={lang} />
        )}
        <Panel>{children}</Panel>
      </div>
    )
  }
  return (
    <div className={`relative flex min-h-0 flex-1 ${nativeCar ? '' : BROWSER_BACKDROP}`}>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-20 transition-opacity duration-700 ease-out"
        style={{ background: COPPER, opacity: tint ? 1 : 0 }}
      />
      <Telemetry lang={lang} car={car} layout={layout} onLayout={onLayout} />
      {main}
    </div>
  )
}

/** The tile a CAR_ACTION belongs to: its window (window controls) or its own name. */
function pickedTile(screen: Screen, anim: CarAnim | null): number {
  if (!anim) return -1
  const key = anim.window ?? anim.action
  return screen.tiles.findIndex((t) => t.id.endsWith(`.${key}`))
}

interface Shared {
  car: CarState | null
  nativeCar: boolean
  layout: TripLayout
  onLayout: (l: TripLayout) => void
}

export function TripView({
  screen,
  anim,
  phase,
  tint,
  onTap,
  ...shared
}: Shared & {
  screen: Screen
  anim: CarAnim | null
  phase: 'in' | 'out'
  tint: boolean
  onTap: (tile: number) => void
}) {
  const picked = pickedTile(screen, anim)
  const locked = anim !== null
  // Half the width in the split layout: one column for three tiles, two for more.
  const cols =
    shared.layout === 'split' ? (screen.tiles.length <= 3 ? 'grid-cols-1' : 'grid-cols-2') : 'grid-cols-3 portrait:grid-cols-2'
  return (
    <TripShell lang={screen.lang} tint={tint} {...shared}>
      {screen.path.length > 0 && (
        <p className="mb-4 text-2xl font-semibold text-white/90">{screen.path.join('  ›  ')}</p>
      )}
      {screen.prompt && (
        // "Plan a trip", a routes screen's drop-off (and why), or a Support question (core/car): large,
        // high contrast, one short line each.
        <p className={`mb-5 max-w-6xl whitespace-pre-line rounded-3xl bg-black/60 px-8 py-4 text-center font-bold leading-snug text-white ${screen.screen === 'support_question' || screen.prompt.indexOf('\n') < 0 ? 'text-5xl' : 'text-3xl'}`}>
          {screen.prompt}
        </p>
      )}
      <div className={`grid w-full max-w-6xl flex-1 auto-rows-fr gap-6 ${cols}`}>
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
  text,
  lang,
  onConfirm,
  onCancel,
  ...shared
}: Shared & {
  action: 'pull_over' | 'support' | 'route'
  text?: string // the route confirm: destination, route, time, drop-off (from the Core)
  lang: Lang
  onConfirm: () => void
  onCancel: () => void
}) {
  const s = STRINGS[lang].trip
  const pull = action === 'pull_over'
  const narrow = shared.layout === 'split'
  return (
    <TripShell lang={lang} tint={pull} {...shared}>
      <div className="flex flex-col items-center rounded-[2.5rem] bg-white/95 px-12 py-10 shadow-2xl shadow-black/15">
        <p className={`mb-8 max-w-5xl text-center font-bold ${action === 'route' ? 'text-5xl leading-tight' : 'text-6xl'} ${pull ? 'text-[#7a2e0e]' : 'text-zinc-900'}`}>
          {action === 'route' && text ? text : pull ? s.pullOver : s.support}
        </p>
        <div className={`flex gap-8 ${narrow ? 'flex-col' : ''}`}>
          <button
            type="button"
            onClick={onConfirm}
            className={`min-w-72 rounded-3xl px-12 py-8 text-5xl font-bold text-white shadow-lg ${pull ? 'bg-[#c05a2c]' : 'bg-sky-700'}`}
          >
            {s.confirm}
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="min-w-72 rounded-3xl bg-white px-12 py-8 text-5xl font-bold text-zinc-900 ring-2 ring-zinc-300"
          >
            {s.cancel}
          </button>
        </div>
        <p className="mt-8 text-center text-2xl text-zinc-500">{s.how}</p>
      </div>
    </TripShell>
  )
}

/** A spoken line during the trip (a confirmed sentence): same layout, the car stays in view. */
export function TripSpeaking({ lang, text, tint, ...shared }: Shared & { lang: Lang; text: string; tint: boolean }) {
  return (
    <TripShell lang={lang} tint={tint} {...shared}>
      <p className="max-w-5xl rounded-[2.5rem] bg-white/95 px-14 py-10 text-center text-5xl font-bold leading-tight text-zinc-900 shadow-2xl shadow-black/15">
        {text}
      </p>
    </TripShell>
  )
}
