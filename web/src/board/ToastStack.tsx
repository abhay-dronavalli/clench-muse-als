import type { Toast, Tone } from './toast'

const TONE: Record<Tone, string> = {
  ok: 'bg-emerald-600 text-white',
  demo: 'bg-zinc-600 text-zinc-100',
  error: 'bg-red-700 text-white',
}

/** Big banners along the top of the board. */
export function ToastStack({ toasts }: { toasts: Toast[] }) {
  if (toasts.length === 0) return null
  return (
    <div className="pointer-events-none fixed inset-x-0 top-4 z-40 flex flex-col items-center gap-3 px-8" role="status">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`max-w-5xl rounded-2xl px-10 py-5 text-center text-4xl font-bold shadow-2xl ${TONE[t.tone]}`}
        >
          {t.text}
        </div>
      ))}
    </div>
  )
}
