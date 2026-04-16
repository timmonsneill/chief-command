import { useEffect, useRef, useState, useCallback } from 'react'

type ConnectionState = 'connecting' | 'connected' | 'disconnected' | 'reconnecting'

interface UseWebSocketOptions {
  /** WebSocket path, e.g. /ws/voice */
  path: string
  /** Auto-connect on mount (default true) */
  autoConnect?: boolean
  /** Called on every incoming text message */
  onMessage?: (data: string) => void
  /** Called on every incoming binary message */
  onBinary?: (data: ArrayBuffer) => void
}

interface UseWebSocketReturn {
  send: (data: string | ArrayBuffer | Blob) => void
  lastMessage: string | null
  isConnected: boolean
  connectionState: ConnectionState
  connect: () => void
  disconnect: () => void
}

const MAX_RETRIES = 10
const BASE_DELAY = 1000

export function useWebSocket({
  path,
  autoConnect = true,
  onMessage,
  onBinary,
}: UseWebSocketOptions): UseWebSocketReturn {
  const wsRef = useRef<WebSocket | null>(null)
  const retriesRef = useRef(0)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const onMessageRef = useRef(onMessage)
  const onBinaryRef = useRef(onBinary)
  onMessageRef.current = onMessage
  onBinaryRef.current = onBinary

  const [lastMessage, setLastMessage] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')

  const getWsUrl = useCallback(() => {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const host = window.location.host
    const token = localStorage.getItem('chief_token') || ''
    return `${proto}//${host}${path}?token=${encodeURIComponent(token)}`
  }, [path])

  const cleanup = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
    if (wsRef.current) {
      wsRef.current.onopen = null
      wsRef.current.onclose = null
      wsRef.current.onmessage = null
      wsRef.current.onerror = null
      wsRef.current.close()
      wsRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    cleanup()
    setConnectionState('connecting')

    const ws = new WebSocket(getWsUrl())
    wsRef.current = ws

    ws.onopen = () => {
      retriesRef.current = 0
      setConnectionState('connected')
    }

    ws.binaryType = 'arraybuffer'

    ws.onmessage = (event) => {
      if (event.data instanceof ArrayBuffer) {
        // Binary frame — audio data
        onBinaryRef.current?.(event.data)
      } else {
        // Text frame — JSON message
        const data = event.data as string
        setLastMessage(data)
        onMessageRef.current?.(data)
      }
    }

    ws.onerror = () => {
      // onclose will fire after this
    }

    ws.onclose = () => {
      setConnectionState('disconnected')

      if (retriesRef.current < MAX_RETRIES) {
        const delay = Math.min(BASE_DELAY * Math.pow(2, retriesRef.current), 30000)
        retriesRef.current += 1
        setConnectionState('reconnecting')
        timerRef.current = setTimeout(() => {
          connect()
        }, delay)
      }
    }
  }, [getWsUrl, cleanup])

  const disconnect = useCallback(() => {
    retriesRef.current = MAX_RETRIES // prevent reconnect
    cleanup()
    setConnectionState('disconnected')
  }, [cleanup])

  const send = useCallback((data: string | ArrayBuffer | Blob) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data)
    }
  }, [])

  useEffect(() => {
    if (autoConnect) {
      connect()
    }
    return () => {
      retriesRef.current = MAX_RETRIES
      cleanup()
    }
  }, [autoConnect, connect, cleanup])

  return {
    send,
    lastMessage,
    isConnected: connectionState === 'connected',
    connectionState,
    connect,
    disconnect,
  }
}
