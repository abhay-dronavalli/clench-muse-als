import { useSyncExternalStore } from 'react'
import { tracker } from './tracker'

/** The shared tracker's status (off, starting, on, error), re-rendering on every change. */
export function useTrackerStatus() {
  return useSyncExternalStore(tracker.subscribeStatus, tracker.getStatus)
}
