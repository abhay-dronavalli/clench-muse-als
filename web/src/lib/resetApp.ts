import { nativeBridge } from '../facetrack/native'
import { stopSensor } from '../sensor/service'

/**
 * Reset the app to its first run (the dev panel's Reset app): the Core forgets the history (every pick
 * and confirmed sentence, so also a loaded demo week), the calibrated head range and the headband, and
 * goes back to its first settings (POST /api/reset); this browser forgets its own settings (cursor dot,
 * gaze tuning); the tablet forgets its eye calibration and disconnects the Muse; then the page reloads
 * to the start screen and the onboarding. The ElevenLabs audio cache is kept. Refused (throws) during a
 * help countdown: a reset never cancels a call for help.
 */
export async function resetApp(): Promise<void> {
  const res = await fetch('/api/reset', { method: 'POST' })
  if (!res.ok) {
    const detail = await res.json().then((b: { detail?: string }) => b.detail, () => undefined)
    throw new Error(detail ?? `HTTP ${res.status}`)
  }
  await stopSensor().catch((e: unknown) => console.warn('reset: the headband did not stop', e))
  try {
    nativeBridge()?.resetApp?.()
  } catch (e) {
    console.warn('reset: the tablet shell did not reset', e)
  }
  try {
    window.localStorage.clear()
    window.sessionStorage.clear()
  } catch (e) {
    console.warn('reset: browser storage not cleared', e)
  }
  window.location.reload()
}
