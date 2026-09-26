import type { SocketStatus } from './useSocket'

const COLOR: Record<SocketStatus, string> = {
  open: 'bg-emerald-400',
  connecting: 'bg-amber-400 animate-pulse',
  closed: 'bg-red-500',
}

export function StatusDot({ status, label }: { status: SocketStatus; label: string }) {
  return (
    <span className="inline-flex items-center gap-2" title={`${label}: ${status}`}>
      <span className={`inline-block h-3 w-3 rounded-full ${COLOR[status]}`} />
      <span className="sr-only">
        {label}: {status}
      </span>
    </span>
  )
}
