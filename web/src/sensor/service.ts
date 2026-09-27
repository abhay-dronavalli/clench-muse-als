/**
 * The Muse Sensor Service: the Core's supervisor (`/api/sensor`, a Python process on the laptop), or,
 * in the Android tablet shell built with a Muse profile, the tablet's own sensor (the headband pairs
 * with the tablet). The panel cannot tell them apart except by `source: 'tablet'`.
 */
import { nativeBridge } from '../facetrack/native'

export type SensorStatus = {
  running: boolean
  pid: number | null
  profile: string | null
  source: string | null
  blink: string
  exit_code: number | null
  profiles: string[]
  log: string[]
}

async function call(path: string, init?: RequestInit): Promise<SensorStatus> {
  const res = await fetch(path, init)
  if (!res.ok) {
    let detail = `${res.status}`
    try {
      detail = (await res.json()).detail ?? detail
    } catch {
      // A proxy error page is not JSON; the status code is all we have.
    }
    throw new Error(detail)
  }
  return (await res.json()) as SensorStatus
}

/** The tablet shell's sensor, when this page runs in one that has it. */
function tabletSensor() {
  const b = nativeBridge()
  try {
    if (b?.museAvailable?.() && b.museStatus && b.museConnect && b.museDisconnect) return b
  } catch {
    // an older or broken bridge: use the Core's supervisor
  }
  return null
}

export const isTabletSensor = () => tabletSensor() !== null

function fromTablet(json: string | undefined): Promise<SensorStatus> {
  try {
    return Promise.resolve(JSON.parse(json ?? '') as SensorStatus)
  } catch (e) {
    return Promise.reject(new Error(`tablet sensor answered badly: ${String(e)}`))
  }
}

export const getSensor = () => {
  const tablet = tabletSensor()
  return tablet ? fromTablet(tablet.museStatus?.()) : call('/api/sensor')
}

export type BlinkMode = 'auto' | 'on' | 'off'

export const startSensor = (profile: string, source: 'muse' | 'demo', blink: BlinkMode = 'auto') => {
  // The tablet's sensor uses the profile it was built with, clenches only.
  const tablet = tabletSensor()
  if (tablet) return fromTablet(tablet.museConnect?.())
  return call('/api/sensor/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile, source, blink }),
  })
}

export const stopSensor = () => {
  const tablet = tabletSensor()
  return tablet ? fromTablet(tablet.museDisconnect?.()) : call('/api/sensor/stop', { method: 'POST' })
}
