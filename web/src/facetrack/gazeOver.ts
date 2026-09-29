import { useEffect, useRef, useState } from 'react'
import { cursor } from './stores'

/**
 * Whether the gaze (or head) point is on this element, for screens without Core tiles (the confirm
 * screens). Display only: nothing is sent. On a confirm screen any clench confirms, so only the
 * Confirm target uses this; Cancel is never lit by gaze (a clench there would still confirm).
 */
export function useGazeOver<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [over, setOver] = useState(false)
  useEffect(
    () =>
      cursor.subscribe(() => {
        const p = cursor.get()
        const el = ref.current
        if (!p || !el) {
          setOver(false)
          return
        }
        const r = el.getBoundingClientRect()
        const x = p.x * window.innerWidth
        const y = p.y * window.innerHeight
        setOver(x >= r.left && x <= r.right && y >= r.top && y <= r.bottom)
      }),
    [],
  )
  return [ref, over] as const
}
