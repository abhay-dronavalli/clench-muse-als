import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { gaze } from '../facetrack/gaze'

export interface SetupOption { id: string; label: string; act: () => void }

/** Local setup input only. Never sends a CLENCH or a confirmation to the Core. */
export function useSetupInput(stage: string, options: SetupOption[], enabled: boolean) {
  const key = `${stage}:${options.map((o) => o.id).join('|')}`
  const [selection, setSelection] = useState({ key, index: 0 })
  const selected = selection.key === key ? selection.index : 0
  const [tracked, setTracked] = useState(false)
  const latest = useRef({ options, enabled })
  useLayoutEffect(() => { latest.current = { options, enabled } })

  useEffect(() => {
    let index = 0
    let lastSeen = -Infinity
    let lastScan = performance.now()
    let holdUntil = 0
    let candidate = -1
    let candidateSince = 0
    const select = (index: number) => setSelection({ key, index })
    const offGaze = gaze.subscribe((sample) => {
      if (!sample.found || !sample.point || !latest.current.enabled) return
      lastSeen = performance.now()
      if (lastSeen < holdUntil) return
      const controls = [...document.querySelectorAll<HTMLElement>('[data-setup-option]')]
      if (controls.length !== latest.current.options.length) return
      const x = sample.point.x * window.innerWidth
      const y = sample.point.y * window.innerHeight
      const hit = controls.findIndex((el) => {
        const r = el.getBoundingClientRect()
        return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom
      })
      if (hit < 0) { candidate = -1; return }
      if (hit !== candidate) { candidate = hit; candidateSince = lastSeen; return }
      if (lastSeen - candidateSince >= 250) { index = hit; select(hit) }
    })
    // No blink input in onboarding (decisions #30): gaze highlights, a touch or the step's timer acts.
    const timer = window.setInterval(() => {
      const at = performance.now()
      const live = gaze.available()
      setTracked(live)
      if (!latest.current.enabled || live || at < holdUntil) { lastScan = at; return }
      if (at - lastScan >= 2200 && latest.current.options.length > 0) {
        index = (index + 1) % latest.current.options.length
        select(index)
        lastScan = at
      }
    }, 100)
    return () => { offGaze(); window.clearInterval(timer) }
  }, [stage, key])
  return { selected, tracked }
}
