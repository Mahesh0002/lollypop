import { create } from 'zustand'
import * as bridge from '../ipc/bridge'
import type { Album, Track } from '../ipc/bridge'

export type ViewId =
  | 'listen-now'
  | 'albums'
  | 'artists'
  | 'playlists'
  | 'album-detail'

export type Playback = {
  playing: boolean
  trackId: number | null
  title: string | null
  artist: string | null
  position: number
  duration: number
  volume: number
}

type LibraryState = {
  /** Bridge handshake completed with the Python host. */
  online: boolean
  albums: Album[]
  total: number
  offset: number
  limit: number
  truncated: boolean
  loading: boolean
  error: string | null
  query: string
  view: ViewId
  /** Currently selected album for detail view */
  selectedAlbum: Album | null
  /** Tracks for the selected album */
  albumTracks: Track[]
  playback: Playback
  /** Ask the host for the library (no-op when offline). */
  load: (offset?: number, limit?: number) => void
  loadMore: () => void
  setQuery: (q: string) => void
  setView: (v: ViewId) => void
  selectAlbum: (album: Album | null) => void
  playTrack: (trackId: number, queue?: number[]) => void
  togglePlay: () => void
  seek: (position: number) => void
  setVolume: (volume: number) => void
}

const idle: Playback = {
  playing: false,
  trackId: null,
  title: null,
  artist: null,
  position: 0,
  duration: 0,
  volume: 1.0,
}

export const useLibrary = create<LibraryState>((set, get) => ({
  online: bridge.connected,
  albums: [],
  total: 0,
  offset: 0,
  limit: 100,
  truncated: false,
  loading: false,
  error: null,
  query: '',
  view: 'listen-now',
  selectedAlbum: null,
  albumTracks: [],
  playback: idle,

  load: (offset = 0, limit = 100) => {
    if (!bridge.connected) {
      set({ loading: false, albums: [], total: 0 })
      return
    }
    if (get().loading) return
    set({ loading: true, error: null, offset, limit })
    bridge.send({ type: 'GET_LIBRARY', payload: { offset, limit } })
  },

  loadMore: () => {
    const { albums, total, offset, limit, loading } = get()
    if (loading || offset + albums.length >= total) return
    get().load(offset + limit, limit)
  },

  setQuery: (query) => set({ query }),

  setView: (view) => {
    set({ view })
    bridge.send({ type: 'NAVIGATE', payload: { view } })
  },

  selectAlbum: (album) => {
    set({ selectedAlbum: album, albumTracks: [] })
    if (album) {
      bridge.send({ type: 'NAVIGATE', payload: { view: 'album', id: album.id } })
    }
  },

  playTrack: (trackId, queue) => {
    bridge.send({ type: 'PLAY', payload: { trackId, queue } })
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

  setVolume: (volume) => {
    const v = Math.max(0, Math.min(1, volume))
    bridge.send({ type: 'SET_VOLUME', payload: { volume: v } })
    set({ playback: { ...get().playback, volume: v } })
  },
}))

/* ===== Host → app subscriptions (module scope, once) ===== */

bridge.on('HOST_READY', () => {
  useLibrary.setState({ online: true })
  useLibrary.getState().load()
})

bridge.on('LIBRARY_LOADED', (payload) => {
  const { albums, total, offset, limit, truncated } = payload
  useLibrary.setState((state) => ({
    albums: offset === 0 ? albums : [...state.albums, ...albums],
    total,
    offset,
    limit,
    truncated,
    loading: false,
  }))
})

bridge.on('ALBUM_LOADED', (payload) => {
  useLibrary.setState({
    selectedAlbum: payload.album,
    albumTracks: payload.tracks,
    view: 'album-detail',
  })
})

bridge.on('TRACKS_LOADED', (payload) => {
  useLibrary.setState({ albumTracks: payload.tracks })
})

bridge.on('SEARCH_RESULTS', (payload) => {
  // For now just log; search view can be added later
  console.info('[search]', payload)
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
      volume: state.volume,
    },
  })
})

bridge.on('PLAYBACK_POSITION', (payload) => {
  const state = useLibrary.getState().playback
  useLibrary.setState({
    playback: {
      ...state,
      position: payload.position,
      duration: payload.duration,
    },
  })
})

bridge.on('ERROR', (payload) => {
  useLibrary.setState({ error: payload.message, loading: false })
})