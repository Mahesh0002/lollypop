import { AlbumGrid } from './AlbumGrid'
import { AlbumDetailPage } from './AlbumDetailPage'
import { TrackList } from './TrackList'
import { useLibrary } from '../store/useLibrary'
import type { ViewId } from '../store/useLibrary'

const TITLES: Record<ViewId, string> = {
  'listen-now': 'Listen Now',
  albums: 'Albums',
  artists: 'Artists',
  playlists: 'Playlists',
  'album-detail': 'Album',
  'artist-detail': 'Artist',
  'playlist-detail': 'Playlist',
  tracks: 'Tracks',
  search: 'Search',
}

export function AppShell() {
  const {
    albums, total, truncated, loading, online, query, view, playback,
    setQuery, setView, selectAlbum, playTrack, togglePlay, seek, load, loadMore, selectedAlbum, albumTracks, artistTracks, artistAlbums, playlistTracks,
  } = useLibrary()

  const needle = query.trim().toLowerCase()
  const visible = needle
    ? albums.filter(
        (album) =>
          album.name.toLowerCase().includes(needle) ||
          album.artists.some((artist) => artist.toLowerCase().includes(needle))
      )
    : albums

  const isDetail = view === 'album-detail' || view === 'artist-detail' || view === 'playlist-detail'

  // Content-only component - no header, no player bar, no mini-player
  // Native GTK handles header, player bar, right panel
  return (
    <main className="webview-content" role="main">
      {isDetail && view === 'album-detail' && selectedAlbum ? (
        <AlbumDetailPage
          album={selectedAlbum}
          tracks={albumTracks}
          onBack={() => setView('albums')}
          onPlayTrack={playTrack}
          onPlayAlbum={() => selectedAlbum && playTrack(selectedAlbum.id)}
          playingTrackId={playback.trackId}
        />
      ) : isDetail && view === 'artist-detail' && selectedAlbum ? (
        // Artist detail view
        <div className="detail">
          <div className="detail__hero">
            <div className="detail__art" aria-hidden="true">
              <span>🎤</span>
            </div>
            <div className="detail__meta">
              <h1 className="detail__title">{selectedAlbum.name}</h1>
              <p className="detail__artist">Artist</p>
              <div className="detail__actions">
                <button type="button" className="btn" onClick={() => artistTracks.length && playTrack(artistTracks[0].id)}>
                  Play
                </button>
                <button type="button" className="btn btn--ghost">Shuffle</button>
              </div>
            </div>
          </div>
          <div className="detail__tracks">
            <h2 className="detail__tracks-title">Top Tracks</h2>
            <TrackList tracks={artistTracks} onPlay={playTrack} playingTrackId={playback.trackId} />
          </div>
        </div>
      ) : isDetail && view === 'playlist-detail' && selectedAlbum ? (
        <div className="detail">
          <div className="detail__hero">
            <div className="detail__art" aria-hidden="true">
              <span>📋</span>
            </div>
            <div className="detail__meta">
              <h1 className="detail__title">{selectedAlbum.name}</h1>
              <p className="detail__artist">Playlist</p>
              <div className="detail__actions">
                <button type="button" className="btn" onClick={() => playlistTracks.length && playTrack(playlistTracks[0].id)}>
                  Play
                </button>
                <button type="button" className="btn btn--ghost">Shuffle</button>
              </div>
            </div>
          </div>
          <div className="detail__tracks">
            <h2 className="detail__tracks-title">Tracks</h2>
            <TrackList tracks={playlistTracks} onPlay={playTrack} playingTrackId={playback.trackId} />
          </div>
        </div>
      ) : (
        <>
          <div className="section__head">
            <h1 className="section__title">
              {needle ? `Results for “${query}”` : TITLES[view]}
            </h1>
            <span className="section__meta">
              {loading
                ? 'Loading…'
                : visible.length > 0
                  ? `${visible.length} of ${total} albums`
                  : ''}
            </span>
          </div>

          {visible.length > 0 ? (
            <AlbumGrid albums={visible} onPlay={playTrack} onSelect={selectAlbum} />
          ) : (
            <div className="empty">
              <h2>{loading ? 'Loading your library' : 'No albums yet'}</h2>
              <p>
                {online
                  ? truncated || !needle
                    ? 'Lollypop reported an empty library.'
                    : 'Nothing matches that search.'
                  : 'Open Lollypop with Shift+Alt+W to connect the library.'}
              </p>
              {!online && (
                <p style={{ marginTop: 'var(--space-4)' }}>
                  <button type="button" className="btn btn--ghost" onClick={() => load()}>
                    Retry connection
                  </button>
                </p>
              )}
            </div>
          )}

          {!loading && truncated && visible.length > 0 && (
            <button
              type="button"
              className="btn btn--ghost"
              style={{ margin: 'var(--space-6) auto 0', display: 'block' }}
              onClick={() => loadMore()}
            >
              Load more ({total - visible.length} remaining)
            </button>
          )}
        </>
      )}
    </main>
  )
}