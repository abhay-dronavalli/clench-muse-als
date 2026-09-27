import { InputLog } from '../sensor/InputLog'
import { MusePanel } from '../sensor/MusePanel'

/** Caregiver input controls; the patient board remains at /. */
export default function ConsolePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-950 text-zinc-100">
      <h1 className="text-5xl font-bold">Clench caregiver console</h1>
      <MusePanel defaultOpen />
      <InputLog />
      <p className="text-zinc-400">Muse panel: press <kbd>,</kbd> · Input log: press <kbd>/</kbd></p>
      <a href="/" className="text-sky-300 underline">Open patient board</a>
      <p className="text-sm text-zinc-500">Prototype for communication. Not a medical device.</p>
    </main>
  )
}
