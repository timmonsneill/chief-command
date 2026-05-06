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
  send: (data: string | ArrayBuffer | Blob) => boolean
  lastMessage: string | null
  isConnected: boolean
  connectionState: ConnectionState
  connect: () => void
  disconnect: () => void
}

const MAX_RETRIES = 10
const BASE_DELAY = 1000

// ─── Module-level connection registry ────────────────────────────────────────
//
// Why this exists: prior to 2026-05-05 each `useWebSocket(...)` call opened its
// own native WebSocket. On `/voice` the page mounts THREE consumers:
//   1. <Layout>                — connection-dot indicator
//   2. <ProjectContextProvider> — listens for `context_switched` frames
//   3. <VoicePage>              — full voice session (audio + transcripts)
// All three pointed at `/ws/voice`, so iPhone page-load was producing three
// concurrent Live sessions, each generating audio for the same turn — Chief's
// voice would barge in on itself ("triple-voice bug", PM2 confirmed three opens
// per page-load at 20:50:08-09 from the same IP/token).
//
// React 18 StrictMode dev double-mounting amplifies this further (effects fire,
// cleanup, fire again synchronously) — production would still produce 3, dev
// would briefly hit 6.
//
// Fix: a module-level registry keyed by URL. The hook becomes a thin
// subscription handle on top of a single shared socket per URL. Refcounted
// open/close: first subscriber opens, last subscriber closes. send() routes
// to the shared socket. Inbound frames fan out to every subscriber's
// onMessage/onBinary callbacks. Reconnect logic lives on the registry, so
// every subscriber sees one consistent connection state.
//
// StrictMode: the synchronous mount→unmount→mount sequence refcounts to 1 → 0
// → 1, but we don't close on refcount=0 immediately; we defer with a small
// grace window so StrictMode's flush-and-remount doesn't tear down the socket
// only to immediately recreate it. The grace also covers fast route swaps
// where the same WS is needed by a different page.

interface Subscriber {
  onMessage?: (data: string) => void
  onBinary?: (data: ArrayBuffer) => void
  onState?: (state: ConnectionState) => void
  onLastMessage?: (data: string) => void
}

interface SharedConnection {
  url: string
  ws: WebSocket | null
  state: ConnectionState
  subscribers: Set<Subscriber>
  retries: number
  retryTimer: ReturnType<typeof setTimeout> | null
  closeTimer: ReturnType<typeof setTimeout> | null
  /** Set when the consumer explicitly called disconnect — suppresses retry. */
  manuallyClosed: boolean
}

const connections = new Map<string, SharedConnection>()

// How long to wait after the last subscriber unsubscribes before actually
// closing the socket. StrictMode dev double-effects cycle in <1ms, real route
// changes are usually <100ms, so 250ms is a safe-but-snappy grace window.
const CLOSE_GRACE_MS = 250

function buildWsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const token = localStorage.getItem('chief_token') || ''
  return `${proto}//${host}${path}?token=${encodeURIComponent(token)}`
}

function notifyState(conn: SharedConnection) {
  for (const sub of conn.subscribers) {
    sub.onState?.(conn.state)
  }
}

function clearTimers(conn: SharedConnection) {
  if (conn.retryTimer) {
    clearTimeout(conn.retryTimer)
    conn.retryTimer = null
  }
  if (conn.closeTimer) {
    clearTimeout(conn.closeTimer)
    conn.closeTimer = null
  }
}

function detachSocket(conn: SharedConnection) {
  const ws = conn.ws
  if (!ws) return
  ws.onopen = null
  ws.onclose = null
  ws.onmessage = null
  ws.onerror = null
  try {
    ws.close()
  } catch {
    /* ignore — socket may already be closing */
  }
  conn.ws = null
}

function openSocket(conn: SharedConnection) {
  // Idempotency guard: if an existing socket is CONNECTING or OPEN, do nothing.
  // This makes `openSocket` safe to call on every subscribe — including the
  // StrictMode double-mount path where two `subscribe` calls land back-to-back.
  if (conn.ws) {
    const rs = conn.ws.readyState
    if (rs === WebSocket.CONNECTING || rs === WebSocket.OPEN) {
      return
    }
  }

  conn.state = 'connecting'
  conn.manuallyClosed = false
  notifyState(conn)

  const ws = new WebSocket(conn.url)
  conn.ws = ws
  ws.binaryType = 'arraybuffer'

  ws.onopen = () => {
    conn.retries = 0
    conn.state = 'connected'
    notifyState(conn)
  }

  ws.onmessage = (event) => {
    if (event.data instanceof ArrayBuffer) {
      // Binary frame — audio data. Fan out to every subscriber that asked
      // for binary frames. Most pages won't (only VoicePage cares).
      for (const sub of conn.subscribers) {
        sub.onBinary?.(event.data)
      }
    } else {
      const data = event.data as string
      for (const sub of conn.subscribers) {
        sub.onLastMessage?.(data)
        sub.onMessage?.(data)
      }
    }
  }

  ws.onerror = () => {
    // onclose will fire after this; let it handle the state transition.
  }

  ws.onclose = () => {
    conn.ws = null
    if (conn.manuallyClosed) {
      conn.state = 'disconnected'
      notifyState(conn)
      return
    }
    conn.state = 'disconnected'
    notifyState(conn)

    if (conn.subscribers.size === 0) {
      // Nobody listening anymore — stop reconnecting and let the registry
      // entry get GC'd by the next acquire.
      return
    }

    if (conn.retries < MAX_RETRIES) {
      const delay = Math.min(BASE_DELAY * Math.pow(2, conn.retries), 30000)
      conn.retries += 1
      conn.state = 'reconnecting'
      notifyState(conn)
      conn.retryTimer = setTimeout(() => {
        conn.retryTimer = null
        if (conn.subscribers.size === 0) return
        openSocket(conn)
      }, delay)
    }
  }
}

function acquire(url: string, sub: Subscriber): SharedConnection {
  let conn = connections.get(url)
  if (!conn) {
    conn = {
      url,
      ws: null,
      state: 'disconnected',
      subscribers: new Set(),
      retries: 0,
      retryTimer: null,
      closeTimer: null,
      manuallyClosed: false,
    }
    connections.set(url, conn)
  }

  // If a close was scheduled (last subscriber went away), cancel it — we have
  // a new subscriber and want to keep the socket alive.
  if (conn.closeTimer) {
    clearTimeout(conn.closeTimer)
    conn.closeTimer = null
  }

  conn.subscribers.add(sub)

  // Push the current state to the new subscriber so it doesn't sit at its
  // initial 'disconnected' default while the shared socket is already OPEN.
  sub.onState?.(conn.state)

  // Open if needed. openSocket() is idempotent — safe to call even if a
  // socket is already CONNECTING/OPEN.
  openSocket(conn)

  return conn
}

function release(url: string, sub: Subscriber) {
  const conn = connections.get(url)
  if (!conn) return
  conn.subscribers.delete(sub)
  if (conn.subscribers.size > 0) return

  // Last subscriber gone. Defer the actual close so a StrictMode double-effect
  // (unmount immediately followed by remount) doesn't tear down a healthy
  // socket only to recreate it.
  if (conn.closeTimer) clearTimeout(conn.closeTimer)
  conn.closeTimer = setTimeout(() => {
    conn.closeTimer = null
    if (conn.subscribers.size > 0) return
    conn.manuallyClosed = true
    clearTimers(conn)
    detachSocket(conn)
    conn.state = 'disconnected'
    connections.delete(url)
  }, CLOSE_GRACE_MS)
}

function sendOn(url: string, data: string | ArrayBuffer | Blob): boolean {
  const conn = connections.get(url)
  const ws = conn?.ws
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(data)
    return true
  }
  console.warn('[WS] send dropped — readyState=', ws?.readyState)
  return false
}

// ─── Public hook ─────────────────────────────────────────────────────────────

export function useWebSocket({
  path,
  autoConnect = true,
  onMessage,
  onBinary,
}: UseWebSocketOptions): UseWebSocketReturn {
  const onMessageRef = useRef(onMessage)
  const onBinaryRef = useRef(onBinary)
  onMessageRef.current = onMessage
  onBinaryRef.current = onBinary

  const [lastMessage, setLastMessage] = useState<string | null>(null)
  const [connectionState, setConnectionState] = useState<ConnectionState>('disconnected')

  // Stable subscriber identity so acquire/release reference the same object
  // across re-renders. The callbacks inside route through the refs above so
  // the consumer can pass fresh closures every render without resubscribing.
  const subRef = useRef<Subscriber | null>(null)
  if (!subRef.current) {
    subRef.current = {
      onState: setConnectionState,
      onLastMessage: setLastMessage,
      onMessage: (data) => onMessageRef.current?.(data),
      onBinary: (data) => onBinaryRef.current?.(data),
    }
  }

  const url = buildWsUrl(path)
  // Mirror current URL into a ref so imperative `send()` always targets the
  // most recent socket without forcing `send` to re-create on every render.
  // For subscribe/unsubscribe pairing we DO NOT use this ref — those capture
  // url in their effect closure to avoid mismatched releases when the url
  // changes between mount and cleanup.
  const urlRef = useRef(url)
  urlRef.current = url

  const send = useCallback((data: string | ArrayBuffer | Blob): boolean => {
    return sendOn(urlRef.current, data)
  }, [])

  // Auto-subscribe on mount if requested. Cleanup releases this hook's
  // subscription against the SAME url it acquired with — captured in this
  // effect's closure, not read from urlRef (which could have moved on by the
  // time cleanup fires after a token/path change).
  useEffect(() => {
    if (!autoConnect) return
    const sub = subRef.current!
    acquire(url, sub)
    return () => {
      release(url, sub)
    }
  }, [autoConnect, url])

  // Imperative connect/disconnect for consumers that opt out of autoConnect.
  // These also pin to the url at call-time via a small per-hook flag so a
  // disconnect() call after a url change still releases the correct entry.
  const imperativeUrlRef = useRef<string | null>(null)
  const connect = useCallback(() => {
    if (imperativeUrlRef.current) return
    const u = urlRef.current
    imperativeUrlRef.current = u
    acquire(u, subRef.current!)
  }, [])

  const disconnect = useCallback(() => {
    const u = imperativeUrlRef.current
    if (!u) return
    imperativeUrlRef.current = null
    release(u, subRef.current!)
  }, [])

  // If imperative connect was used and the url later changes (token rotated,
  // path swapped), re-acquire on the new url and release the old one.
  useEffect(() => {
    const old = imperativeUrlRef.current
    if (!old || old === url) return
    release(old, subRef.current!)
    imperativeUrlRef.current = url
    acquire(url, subRef.current!)
  }, [url])

  return {
    send,
    lastMessage,
    isConnected: connectionState === 'connected',
    connectionState,
    connect,
    disconnect,
  }
}

// ─── Test/debug helpers (non-public — used by the dev console only) ──────────
//
// Exposed for the manual smoke test described in the bug report: open the
// /voice route, then in the browser console run
//     window.__wsRegistrySize?.()
// to confirm exactly ONE entry exists for /ws/voice. Used in lieu of a full
// jsdom test harness (this codebase has no vitest/jest config).
declare global {
  interface Window {
    __wsRegistrySize?: () => Record<string, number>
  }
}
if (typeof window !== 'undefined') {
  window.__wsRegistrySize = () => {
    const out: Record<string, number> = {}
    for (const [url, conn] of connections.entries()) {
      out[url] = conn.subscribers.size
    }
    return out
  }
}
