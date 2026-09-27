import { InputLog } from '../sensor/InputLog'
import { MuseInput } from '../sensor/MuseInput'

/** Caregiver input controls; the patient board remains at /. */
export default function ConsolePage() {
  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-zinc-950 text-zinc-100">
      <h1 className="text-5xl font-bold">Clench caregiver console</h1>
      <MuseInput />
      <InputLog />
      <a href="/" className="text-sky-300 underline">Open patient board</a>
      <p className="text-sm text-zinc-500">Prototype for communication. Not a medical device.</p>
    </main>
  )
}
