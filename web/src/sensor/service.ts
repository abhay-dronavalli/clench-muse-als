/** The Core's Muse Sensor Service supervisor (`/api/sensor`). */

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

export const getSensor = () => call('/api/sensor')

export type BlinkMode = 'auto' | 'on' | 'off'

export const startSensor = (profile: string, source: 'muse' | 'demo', blink: BlinkMode = 'auto') =>
  call('/api/sensor/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile, source, blink }),
  })

export const stopSensor = () => call('/api/sensor/stop', { method: 'POST' })
