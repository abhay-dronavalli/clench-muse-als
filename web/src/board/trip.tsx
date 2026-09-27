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
 */

const COPPER = 'rgba(184, 98, 58, 0.22)' // the pull-over tint: warm rust, low contrast

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
      <span className="tabular-nums text-zinc-200">{value}</span>
    </span>
  )
  return (
    <div className="flex shrink-0 items-center justify-center gap-12 border-b border-white/5 bg-black/30 py-2 text-xl">
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
        <div className="flex h-4/5 w-1/2 items-end justify-center rounded-[50%] bg-[radial-gradient(ellipse_at_center,rgba(120,140,170,0.18),transparent_65%)] pb-6">
          <span className="text-lg text-zinc-500">{STRINGS[lang].trip.carHere}</span>
        </div>
      )}
    </div>
  )
}

/** Status strip, car area, and whatever goes below (the tiles, the pull-over confirm, a spoken line). */
function TripShell({ lang, nativeCar, tint, children }: { lang: Lang; nativeCar: boolean; tint: boolean; children: ReactNode }) {
  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
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
                    ? 'bg-[#3a2016] text-orange-100 ring-[10px] ring-[#c8744a]'
                    : 'bg-zinc-800/95 text-yellow-100 ring-[10px] ring-yellow-300'
                  : safety
                    ? 'bg-zinc-900/90 text-orange-100 ring-2 ring-[#8a4a2e]'
                    : 'bg-zinc-900/90 text-zinc-100 ring-2 ring-zinc-700',
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
      <p className="mb-10 text-6xl font-bold text-orange-100">{s.pullOver}</p>
      <div className="flex gap-10">
        <button
          type="button"
          onClick={onConfirm}
          className="min-w-72 rounded-3xl bg-[#3a2016] px-12 py-10 text-5xl font-bold text-orange-100 ring-[10px] ring-[#c8744a]"
        >
          {s.confirm}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="min-w-72 rounded-3xl bg-zinc-900/90 px-12 py-10 text-5xl font-bold text-zinc-100 ring-2 ring-zinc-600"
        >
          {s.cancel}
        </button>
      </div>
      <p className="mt-10 text-2xl text-zinc-400">{s.how}</p>
    </TripShell>
  )
}

/** A spoken line during the trip (Pull over's sentence): same layout, the car stays in view. */
export function TripSpeaking({ lang, text, nativeCar, tint }: { lang: Lang; text: string; nativeCar: boolean; tint: boolean }) {
  return (
    <TripShell lang={lang} nativeCar={nativeCar} tint={tint}>
      <p className="max-w-5xl text-center text-5xl font-bold leading-tight text-zinc-100">{text}</p>
    </TripShell>
  )
}
