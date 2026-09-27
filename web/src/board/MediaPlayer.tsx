// Computer > YouTube / Spotify: the provider's own embedded player, in this page (the person's own
// browser, so their YouTube / Spotify login applies). The Core sends MEDIA; the control tiles under
// the player are ordinary board tiles (scan, head, gaze, dwell and the headband all pick them).

import { useEffect, useRef } from 'react'
import type { Media, Screen } from '../contracts'

export interface NowPlaying {
  provider: 'youtube' | 'spotify'
  id: string
  title: string
}

type Command = Exclude<Media['action'], 'play' | 'stop'>

interface SpotifyController {
  play(): void
  pause(): void
  resume(): void
  togglePlay(): void
  seek(seconds: number): void
  destroy(): void
  addListener(event: string, fn: (e: unknown) => void): void
}
interface SpotifyIFrameAPI {
  createController(el: HTMLElement, options: Record<string, unknown>, cb: (c: SpotifyController) => void): void
}
declare global {
  interface Window {
    onSpotifyIframeApiReady?: (api: SpotifyIFrameAPI) => void
    clenchSpotifyApi?: SpotifyIFrameAPI
  }
}

let spotifyApi: Promise<SpotifyIFrameAPI> | null = null
function loadSpotifyApi(): Promise<SpotifyIFrameAPI> {
  if (window.clenchSpotifyApi) return Promise.resolve(window.clenchSpotifyApi)
  if (!spotifyApi) {
    spotifyApi = new Promise((resolve) => {
      window.onSpotifyIframeApiReady = (api) => {
        window.clenchSpotifyApi = api
        resolve(api)
      }
      const script = document.createElement('script')
      script.src = 'https://open.spotify.com/embed/iframe-api/v1'
      script.async = true
      document.body.appendChild(script)
    })
  }
  return spotifyApi
}

/** A YouTube embed command, for the iframe's postMessage (enablejsapi=1). */
export function youtubeCommand(func: string, args: unknown[] = []): string {
  return JSON.stringify({ event: 'command', func, args })
}

export function MediaPlayer({ media, command }: { media: NowPlaying; command: { action: Command; n: number } | null }) {
  const frame = useRef<HTMLIFrameElement>(null)
  const spot = useRef<HTMLDivElement>(null)
  const controller = useRef<SpotifyController | null>(null)
  const volume = useRef(60)

  // Spotify: its controller API.
  useEffect(() => {
    if (media.provider !== 'spotify' || !spot.current) return
    let live = true
    const host = document.createElement('div')
    spot.current.replaceChildren(host)
    loadSpotifyApi().then((api) => {
      if (!live) return
      api.createController(host, { uri: `spotify:playlist:${media.id}`, width: '100%', height: '100%' }, (c) => {
        controller.current = c
        c.addListener('ready', () => c.play())
      })
    })
    return () => {
      live = false
      controller.current?.destroy()
      controller.current = null
    }
  }, [media.provider, media.id])

  // Commands from the Core (the control tiles).
  useEffect(() => {
    if (!command) return
    if (media.provider === 'youtube') {
      const w = frame.current?.contentWindow
      if (!w) return
      const send = (func: string, args: unknown[] = []) => w.postMessage(youtubeCommand(func, args), '*')
      if (command.action === 'pause') send('pauseVideo')
      else if (command.action === 'resume') send('playVideo')
      else if (command.action === 'restart') {
        send('seekTo', [0, true])
        send('playVideo')
      } else {
        volume.current = Math.max(0, Math.min(100, volume.current + (command.action === 'volume_up' ? 20 : -20)))
        send('unMute')
        send('setVolume', [volume.current])
      }
    } else {
      const c = controller.current
      if (!c) return
      if (command.action === 'pause') c.pause()
      else if (command.action === 'resume') c.resume()
      else if (command.action === 'restart') {
        c.seek(0)
        c.resume()
      }
    }
  }, [command, media.provider])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 px-8 pt-4">
      <p className="truncate text-3xl font-semibold text-zinc-200">{media.title}</p>
      {media.provider === 'youtube' ? (
        <iframe
          ref={frame}
          key={media.id}
          title={media.title}
          className="min-h-0 w-full flex-1 rounded-3xl bg-black"
          src={`https://www.youtube.com/embed/${media.id}?autoplay=1&enablejsapi=1&rel=0&playsinline=1&origin=${encodeURIComponent(location.origin)}`}
          allow="autoplay; encrypted-media; picture-in-picture; fullscreen"
        />
      ) : (
        <div ref={spot} className="min-h-0 w-full flex-1 overflow-hidden rounded-3xl bg-black [&_iframe]:h-full [&_iframe]:w-full" />
      )}
    </div>
  )
}

/** The player's control tiles: one big row, measured by pointing like any tiles (data-tile-index). */
export function ControlRow({ screen }: { screen: Screen }) {
  return (
    <div className="grid h-56 shrink-0 gap-6 px-8 pb-10 pt-4" style={{ gridTemplateColumns: `repeat(${screen.tiles.length}, minmax(0, 1fr))` }}>
      {screen.tiles.map((tile, i) => {
        const on = i === screen.highlight
        return (
          <div
            key={tile.id}
            data-tile-index={i}
            aria-current={on}
            className={[
              'flex items-center justify-center rounded-3xl p-4 text-center text-5xl font-bold transition-transform duration-150',
              on ? 'scale-105 bg-yellow-300 text-zinc-900 ring-8 ring-yellow-100' : 'bg-zinc-800 text-white ring-2 ring-zinc-600',
            ].join(' ')}
          >
            {tile.label}
          </div>
        )
      })}
    </div>
  )
}
