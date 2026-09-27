import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react'
import type { Lang } from '../contracts'
import { eyedidWeb } from './eyedidWeb'
import { boardGrid, tileCenter, validationPasses, type Frac } from './gazeCheck'

// What the board asks once Eyedid web is on, copied from the tablet shell (BoardActivity.kt):
//   no saved calibration  "The eye tracker is not calibrated yet"  Calibrate now / Later
//   a saved calibration   one dot on a random tile; the median gaze must land on that tile
//   the check missed      "The calibration looks off from here"  Recalibrate / Try the check again / Keep it
// The buttons are for the caregiver's mouse. The board holds still while the dot shows.

const TEXT = {
  en: {
    uncalibrated: 'The eye tracker is not calibrated yet.',
    calibrateNow: 'Calibrate now',
    later: 'Later',
    checking: 'Checking the eye calibration: look at the dot',
    failed: 'The eye calibration looks off from here.',
    recalibrate: 'Recalibrate',
    again: 'Try the check again',
    keep: 'Keep it',
  },
  es: {
    uncalibrated: 'El seguimiento de ojos aún no está calibrado.',
    calibrateNow: 'Calibrar ahora',
    later: 'Más tarde',
    checking: 'Comprobando la calibración: mira el punto',
    failed: 'La calibración de ojos parece desviada desde aquí.',
    recalibrate: 'Volver a calibrar',
    again: 'Comprobar otra vez',
    keep: 'Dejarla así',
  },
} as const

// BoardActivity VALIDATE_SETTLE_MS / VALIDATE_COLLECT_MS
const SETTLE_MS = 800
const COLLECT_MS = 1500

function Prompt({ text, buttons }: { text: string; buttons: [string, () => void][] }) {
  return (
    <div role="alertdialog" className="fixed inset-0 z-40 flex items-center justify-center bg-black/70">
      <div className="w-[min(90vw,44rem)] rounded-3xl bg-zinc-900 p-10 text-center text-white shadow-2xl ring-4 ring-sky-400">
        <p className="text-4xl font-bold">{text}</p>
        <div className="mt-8 flex flex-wrap justify-center gap-4">
          {buttons.map(([label, onClick], i) => (
            <button key={label} type="button" onClick={onClick}
              className={`rounded-2xl px-6 py-3 text-2xl font-semibold ${i === 0 ? 'bg-sky-400 text-zinc-950' : 'border border-zinc-600 text-zinc-200'}`}>
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function Check({ lang, tile }: { lang: Lang; tile: number }) {
  const target = useMemo(() => {
    const { cols, rows } = boardGrid(window.innerWidth, window.innerHeight)
    return { ...tileCenter(tile, cols, rows), cols, rows }
  }, [tile])
  const samples = useRef<Frac[]>([])

  useEffect(() => {
    eyedidWeb.hold(true)
    let collecting = false
    const off = eyedidWeb.onRaw((g) => {
      if (collecting && g.found) samples.current.push({ x: g.x, y: g.y })
    })
    const start = window.setTimeout(() => (collecting = true), SETTLE_MS)
    const finish = window.setTimeout(() => {
      collecting = false
      eyedidWeb.checked(validationPasses(samples.current, target, 0.5 / target.cols, 0.5 / target.rows))
    }, SETTLE_MS + COLLECT_MS)
    return () => {
      off()
      window.clearTimeout(start)
      window.clearTimeout(finish)
      eyedidWeb.hold(false)
    }
  }, [target])

  return (
    <div role="dialog" aria-label="Eye calibration check" className="fixed inset-0 z-40 bg-black/85 text-white">
      <div className="absolute -translate-x-1/2 -translate-y-1/2" style={{ left: `${target.x * 100}%`, top: `${target.y * 100}%` }}>
        <div className="h-16 w-16 animate-pulse rounded-full border-8 border-sky-400 bg-red-500" />
      </div>
      <p className="pointer-events-none absolute inset-x-0 bottom-10 text-center text-3xl font-semibold text-zinc-300">
        {TEXT[lang].checking}
      </p>
    </div>
  )
}

export function EyeSetupOverlay({ lang, onCalibrate }: { lang: Lang; onCalibrate: () => void }) {
  useSyncExternalStore(eyedidWeb.subscribe, eyedidWeb.snapshot)
  const t = TEXT[lang]
  switch (eyedidWeb.setup) {
    case 'uncalibrated':
      return <Prompt text={t.uncalibrated} buttons={[[t.calibrateNow, onCalibrate], [t.later, eyedidWeb.dismissSetup]]} />
    case 'check':
      return <Check key={eyedidWeb.checkTile} lang={lang} tile={eyedidWeb.checkTile} />
    case 'check_failed':
      return (
        <Prompt text={t.failed}
          buttons={[[t.recalibrate, onCalibrate], [t.again, eyedidWeb.checkAgain], [t.keep, eyedidWeb.dismissSetup]]} />
      )
    default:
      return null
  }
}
