/**
 * Typed wrapper over `window.pybridge`, injected into every page by
 * lollypop/web_ipc.py at document start (UserScriptInjectionTime.START).
 *
 * Outside the app (plain browser / Playwright) the shim is absent, so we
 * fall back to a local stub that logs instead of throwing. That keeps the
 * frontend developable and testable with `vite dev`.
 */

export type PayloadMap = {
  HOST_READY: { version: string; protocol: number }
  LIBRARY_LOADED: {
    albums: Album[]
    total: number
    truncated: boolean
  }
  PLAYBACK_STATE: {
    playing: boolean
    trackId?: number
    position?: number
    duration?: number
  }
  ERROR: { message: string }
}

export type MessageOf<T extends keyof PayloadMap> = {
  type: T
  payload: PayloadMap[T]
}

export type Outbound =
  | { type: 'GET_LIBRARY' }
  | { type: 'NAVIGATE'; payload: { view: string; id?: number } }
  | { type: 'PLAY'; payload: { trackId: number; queue?: number[] } }
  | { type: 'PAUSE' }
  | { type: 'SEEK'; payload: { position: number } }
  | { type: 'SEARCH'; payload: { query: string } }

export interface Album {
  id: number
  name: string
  artists: string[]
  year?: number | null
  duration?: number
  uri?: string
}

type Handler<T extends keyof PayloadMap> = (
  payload: PayloadMap[T],
  message: MessageOf<T>
) => void
type AnyHandler = (message: { type: string; payload?: unknown }) => void

interface Shim {
  send(message: unknown): void
  on(type: string, cb: (payload: unknown, message: unknown) => void): () => void
}

declare global {
  interface Window {
    pybridge?: Shim
  }
}

const hasHost = typeof window !== 'undefined' && !!window.pybridge

/** Log-only stub so the app runs in a browser during development. */
function stubBridge(): Shim {
  const handlers = new Map<string, Set<AnyHandler>>()
  return {
    send(message) {
      // eslint-disable-next-line no-console
      console.info('[stub pybridge →]', message)
    },
    on(type, cb) {
      const set = handlers.get(type) ?? new Set<AnyHandler>()
      set.add(cb as AnyHandler)
      handlers.set(type, set)
      return () => set.delete(cb as AnyHandler)
    },
  }
}

const bridge: Shim = window.pybridge ?? stubBridge()

/** True when running inside the Lollypop WebView. */
export const connected = hasHost

export function send<T extends Outbound['type']>(
  message: Extract<Outbound, { type: T }>
): void {
  bridge.send(message)
}

export function on<T extends keyof PayloadMap>(
  type: T,
  handler: Handler<T>
): () => void {
  return bridge.on(type, handler as unknown as (payload: unknown, message: unknown) => void)
}

/** Wildcard listener — every inbound message, payload untyped. */
export function onAny(handler: AnyHandler): () => void {
  return bridge.on('*', handler as unknown as (payload: unknown, message: unknown) => void)
}