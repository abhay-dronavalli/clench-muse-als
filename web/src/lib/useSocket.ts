import { useCallback, useEffect, useRef, useState } from 'react'
import type { Message } from '../contracts'

export type SocketStatus = 'connecting' | 'open' | 'closed'
export type Send = (msg: Message) => boolean

export interface LocalTransport {
  send: Send
  subscribe: (receive: (msg: Message) => void) => () => void
}
declare global { interface Window { clenchTransport?: LocalTransport } }

interface Options {
  /** Connect only while true (default true). */
  enabled?: boolean
  onOpen?: (send: Send) => void
  onMessage?: (msg: Message, send: Send) => void
}

const MAX_RETRY_MS = 5000

/** Same-origin WebSocket URL; the Vite dev server proxies /ws/* to the Core. */
function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}${path}`
}

/** WebSocket to the Core with automatic reconnect (backoff up to 5 s). */
export function useSocket(path: string, { enabled = true, onOpen, onMessage }: Options = {}) {
  const [status, setStatus] = useState<SocketStatus>(() => window.clenchTransport ? 'open' : 'closed')
  const wsRef = useRef<WebSocket | null>(null)
  const handlers = useRef({ onOpen, onMessage })
  useEffect(() => {
    handlers.current = { onOpen, onMessage }
  })

  const send = useCallback<Send>((msg) => {
    if (window.clenchTransport) return window.clenchTransport.send(msg)
    const ws = wsRef.current
    if (ws?.readyState !== WebSocket.OPEN) return false
    ws.send(JSON.stringify(msg))
    return true
  }, [])

  useEffect(() => {
    if (!enabled) return
    if (window.clenchTransport) {
      const stop = window.clenchTransport.subscribe(msg => handlers.current.onMessage?.(msg, send))
      handlers.current.onOpen?.(send)
      return stop
    }
    let stopped = false
    let attempt = 0
    let retry: number | undefined

    const connect = () => {
      setStatus('connecting')
      const ws = new WebSocket(wsUrl(path))
      wsRef.current = ws
      ws.onopen = () => {
        attempt = 0
        setStatus('open')
        handlers.current.onOpen?.(send)
      }
      ws.onmessage = (e) => {
        let msg: unknown
        try {
          msg = JSON.parse(String(e.data))
        } catch {
          console.warn('ignored non-JSON message', e.data)
          return
        }
        if (msg && typeof msg === 'object' && typeof (msg as { type?: unknown }).type === 'string') {
          handlers.current.onMessage?.(msg as Message, send)
        }
      }
      ws.onclose = () => {
        if (wsRef.current === ws) wsRef.current = null
        if (stopped) return
        setStatus('closed')
        retry = window.setTimeout(connect, Math.min(MAX_RETRY_MS, 500 * 2 ** attempt++))
      }
    }

    connect()
    return () => {
      stopped = true
      window.clearTimeout(retry)
      wsRef.current?.close()
      wsRef.current = null
      setStatus('closed')
    }
  }, [path, enabled, send])

  return { status, send }
}
