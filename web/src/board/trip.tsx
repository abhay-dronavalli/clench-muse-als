import { useEffect, useState, type ReactNode } from 'react'
import type { Lang, Screen } from '../contracts'
import type { CarAnim } from './carAnimation'
import { STRINGS } from './strings'

/**
 * The trip screen (SCREEN `screen: "trip"`, core/trip.py): a quiet status strip, the 3D car in the
 * upper part, and exactly six big tiles in the middle of the screen (gaze is least accurate at the
 * edges). The car itself is drawn by the tablet shell behind this page (SceneView, see native.ts); in a
 * normal browser the car area only holds a soft placeholder.
 *
 * A picked control (CAR_ACTION) plays one calm sequence while the Core locks input for `ms`: the other
 * five tiles fade out, the picked one stays and grows a little (the rider's eyes keep an anchor), then
 * everything fades back. Pull over is heavier and plainer: a warm copper tint, no scale, a still car.
 * No flashing anywhere: every change is a slow opacity or scale transition.
 *
 * Light theme: white cards and a white status strip over the daytime scene (sky, road, grass).
 */

const COPPER = 'rgba(192, 90, 44, 0.16)' // the pull-over tint: warm rust, low contrast
// In a browser (no 3D scene behind the page): a soft sky-to-grass backdrop in its place.
const BROWSER_BACKDROP = 'bg-[linear-gradient(to_bottom,#b9d9f4_0%,#e4f1fb_42%,#d3ebc8_62%,#a4d18f_100%)]'

/** Mock ride numbers: speed drifts gently, arrival counts down, battery sits still. */
function useRide() {
  const [ride, setRide] = useState({ speed: 31, eta: 14, battery: 78 })
  useEffect(() => {
    const timer = window.setInterval(() => {
      setRide((r) => ({
        speed: Math.max(18, Math.min(42, r.speed + Math.round((Math.random() - 0.5) * 4))),
        eta: Math.max(1, r.eta - (Math.random() < 0.15 ? 1 : 0)),
        battery: r.battery,
      }))
    }, 4000)
    return () => window.clearInterval(timer)
  }, [])
  return ride
}

function StatusStrip({ lang }: { lang: Lang }) {
  const s = STRINGS[lang].trip
  const ride = useRide()
  const item = (label: string, value: string) => (
    <span className="flex items-baseline gap-2">
      <span className="text-zinc-500">{label}</span>
      <span className="font-semibold tabular-nums text-zinc-900">{value}</span>
    </span>
  )
  return (
    <div className="flex shrink-0 items-center justify-center gap-12 border-b border-black/5 bg-white/85 py-2 text-xl shadow-sm backdrop-blur">
      {item(s.speed, `${ride.speed} ${s.mph}`)}
      {item(s.eta, `${ride.eta} ${s.min}`)}
      {item(s.battery, `${ride.battery}%`)}
    </div>
  )
}

/** The upper ~40%: empty (the tablet draws the car behind it), or a soft placeholder in a browser. */
function CarArea({ nativeCar, lang }: { nativeCar: boolean; lang: Lang }) {
  return (
    <div className="relative flex min-h-0 flex-[4] items-center justify-center">
      {!nativeCar && (
        <div className="flex h-4/5 w-1/2 items-end justify-center pb-6">
          <span className="rounded-full bg-white/70 px-4 py-1 text-lg text-zinc-600">{STRINGS[lang].trip.carHere}</span>
        </div>
      )}
    </div>
  )
}

/** Status strip, car area, and whatever goes below (the tiles, the pull-over confirm, a spoken line). */
function TripShell({ lang, nativeCar, tint, children }: { lang: Lang; nativeCar: boolean; tint: boolean; children: ReactNode }) {
  return (
    <div className={`relative flex min-h-0 flex-1 flex-col ${nativeCar ? '' : BROWSER_BACKDROP}`}>
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 z-0 transition-opacity duration-700 ease-out"
        style={{ background: COPPER, opacity: tint ? 1 : 0 }}
      />
      <StatusStrip lang={lang} />
      <CarArea nativeCar={nativeCar} lang={lang} />
      <div className="relative z-10 flex min-h-0 flex-[6] flex-col items-center justify-center px-8 pb-10">{children}</div>
    </div>
  )
}

export function TripView({
  screen,
  anim,
  phase,
  tint,
  nativeCar,
  onTap,
}: {
  screen: Screen
  anim: CarAnim | null
  phase: 'in' | 'out'
  tint: boolean
  nativeCar: boolean
  onTap: (tile: number) => void
}) {
  const picked = anim ? screen.tiles.findIndex((t) => t.id === `trip.${anim.action}`) : -1
  const locked = anim !== null
  return (
    <TripShell lang={screen.lang} nativeCar={nativeCar} tint={tint}>
      <div className="grid h-full max-h-[34rem] w-full max-w-6xl grid-cols-3 grid-rows-2 gap-10 portrait:grid-cols-2 portrait:grid-rows-3">
        {screen.tiles.map((tile, i) => {
          const on = !locked && i === screen.highlight
          const isPicked = i === picked
          const hidden = locked && phase === 'out' && !isPicked
          const grow = isPicked && phase === 'out' && anim?.action !== 'pull_over'
          const safety = tile.id === 'trip.pull_over'
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
                    : 'bg-white text-zinc-900 shadow-xl shadow-black/10 ring-1 ring-black/10',
              ].join(' ')}
            >
              {tile.label}
            </div>
          )
        })}
      </div>
    </TripShell>
  )
}

/** Pull over's confirm: plain, heavy, copper; Confirm and Cancel are big targets for a tap. */
export function PullOverConfirm({
  lang,
  nativeCar,
  onConfirm,
  onCancel,
}: {
  lang: Lang
  nativeCar: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  const s = STRINGS[lang].trip
  return (
    <TripShell lang={lang} nativeCar={nativeCar} tint>
      <div className="flex flex-col items-center rounded-[2.5rem] bg-white/92 px-16 py-12 shadow-2xl shadow-black/15">
        <p className="mb-10 text-6xl font-bold text-[#7a2e0e]">{s.pullOver}</p>
        <div className="flex gap-10">
          <button
            type="button"
            onClick={onConfirm}
            className="min-w-72 rounded-3xl bg-[#c05a2c] px-12 py-10 text-5xl font-bold text-white shadow-lg"
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

/** A spoken line during the trip (Pull over's sentence): same layout, the car stays in view. */
export function TripSpeaking({ lang, text, nativeCar, tint }: { lang: Lang; text: string; nativeCar: boolean; tint: boolean }) {
  return (
    <TripShell lang={lang} nativeCar={nativeCar} tint={tint}>
      <p className="max-w-5xl rounded-[2.5rem] bg-white/92 px-14 py-10 text-center text-5xl font-bold leading-tight text-zinc-900 shadow-2xl shadow-black/15">
        {text}
      </p>
    </TripShell>
  )
}
