import { create } from 'zustand'
import * as bridge from '../ipc/bridge'
import type { Album } from '../ipc/bridge'

export type ViewId = 'listen-now' | 'albums' | 'artists' | 'playlists'

export type Playback = {
  playing: boolean
  trackId: number | null
  title: string | null
  artist: string | null
  position: number
  duration: number
}

type LibraryState = {
  /** Bridge handshake completed with the Python host. */
  online: boolean
  albums: Album[]
  total: number
  truncated: boolean
  loading: boolean
  error: string | null
  query: string
  view: ViewId
  playback: Playback
  /** Ask the host for the library (no-op when offline). */
  load: () => void
  setQuery: (q: string) => void
  setView: (v: ViewId) => void
  playAlbum: (albumId: number) => void
  togglePlay: () => void
  seek: (position: number) => void
}

const idle: Playback = {
  playing: false,
  trackId: null,
  title: null,
  artist: null,
  position: 0,
  duration: 0,
}

export const useLibrary = create<LibraryState>((set, get) => ({
  online: bridge.connected,
  albums: [],
  total: 0,
  truncated: false,
  loading: false,
  error: null,
  query: '',
  view: 'listen-now',
  playback: idle,

  load: () => {
    if (!bridge.connected) {
      // Running in a plain browser: nothing to ask, show the empty state.
      set({ loading: false, albums: [], total: 0 })
      return
    }
    if (get().loading) return
    set({ loading: true, error: null })
    bridge.send({ type: 'GET_LIBRARY' })
  },

  setQuery: (query) => set({ query }),

  setView: (view) => {
    set({ view })
    bridge.send({ type: 'NAVIGATE', payload: { view } })
  },

  playAlbum: (trackId) => {
    const { albums } = get()
    const queue = albums.map((a) => a.id)
    bridge.send({ type: 'PLAY', payload: { trackId, queue } })
    const album = albums.find((a) => a.id === trackId)
    set({
      playback: {
        ...get().playback,
        playing: true,
        trackId,
        title: album?.name ?? null,
        artist: album?.artists[0] ?? null,
      },
    })
  },

  togglePlay: () => {
    const { playback } = get()
    const playing = !playback.playing
    if (playing && playback.trackId != null) {
      bridge.send({ type: 'PLAY', payload: { trackId: playback.trackId } })
    } else {
      bridge.send({ type: 'PAUSE' })
    }
    set({ playback: { ...playback, playing } })
  },

  seek: (position) => {
    bridge.send({ type: 'SEEK', payload: { position } })
    set({ playback: { ...get().playback, position } })
  },
}))

/* ===== Host → app subscriptions (module scope, once) ===== */

bridge.on('HOST_READY', () => {
  useLibrary.setState({ online: true })
  useLibrary.getState().load()
})

bridge.on('LIBRARY_LOADED', (payload) => {
  useLibrary.setState({
    albums: payload.albums,
    total: payload.total,
    truncated: payload.truncated,
    loading: false,
  })
})

bridge.on('PLAYBACK_STATE', (payload) => {
  const state = useLibrary.getState().playback
  useLibrary.setState({
    playback: {
      playing: payload.playing ?? state.playing,
      trackId: payload.trackId ?? state.trackId,
      position: payload.position ?? state.position,
      duration: payload.duration ?? state.duration,
      title: state.title,
      artist: state.artist,
    },
  })
})

bridge.on('ERROR', (payload) => {
  useLibrary.setState({ error: payload.message, loading: false })
})