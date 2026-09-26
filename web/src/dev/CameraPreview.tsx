import { useEffect, useRef, useState } from 'react'
import { useTrackerStatus } from '../facetrack/useTrackerStatus'
import { tracker, type TrackerStatus } from '../facetrack/tracker'

const NUMBERS_MS = 125 // refresh the yaw / pitch numbers about 8 times a second

function statusText(s: TrackerStatus): string {
  switch (s.kind) {
    case 'off':
      return 'camera off (Scan / Head tilt mode)'
    case 'starting':
      return 'starting camera…'
    case 'on':
      return `tracking (${s.delegate})`
    case 'error':
      return `camera problem: ${s.problem}`
  }
}

/**
 * Small mirrored preview of the webcam with the tracked yaw and pitch, for the dev panel. The
 * video is the same local stream the tracker reads; it is never sent anywhere.
 */
export function CameraPreview({ onRetry }: { onRetry: () => void }) {
  const status = useTrackerStatus()
  const video = useRef<HTMLVideoElement>(null)
  const [pose, setPose] = useState<{ face: boolean; yaw: number; pitch: number } | null>(null)

  useEffect(() => {
    const el = video.current
    if (!el) return
    el.srcObject = status.kind === 'on' ? tracker.stream : null
  }, [status])

  useEffect(() => {
    let last = 0
    return tracker.subscribeSample((s) => {
      if (s.t - last < NUMBERS_MS) return
      last = s.t
      setPose(s.angles ? { face: true, yaw: s.angles.yaw, pitch: s.angles.pitch } : { face: false, yaw: 0, pitch: 0 })
    })
  }, [])

  const on = status.kind === 'on'
  return (
    <div className="mb-3">
      <div className="flex items-center justify-between text-xs text-zinc-400">
        <span className={status.kind === 'error' ? 'text-red-400' : undefined}>{statusText(status)}</span>
        {status.kind === 'error' && (
          <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={onRetry}
            className="rounded bg-zinc-700 px-1.5 text-zinc-200 hover:bg-zinc-600">
            retry
          </button>
        )}
      </div>
      {on && (
        <div className="mt-1 flex items-start gap-2">
          <video ref={video} autoPlay muted playsInline className="h-24 w-32 -scale-x-100 rounded bg-black object-cover" />
          <div className="font-mono text-xs leading-5 text-zinc-300">
            {pose?.face ? (
              <>
                <div>yaw {pose.yaw.toFixed(1)}°</div>
                <div>pitch {pose.pitch.toFixed(1)}°</div>
                <div className="text-emerald-400">face</div>
              </>
            ) : (
              <div className="text-amber-300">no face</div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
