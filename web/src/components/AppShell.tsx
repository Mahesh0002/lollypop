import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { PlayerBar } from './PlayerBar'
import { AlbumGrid } from './AlbumGrid'
import { AlbumDetailPage } from './AlbumDetailPage'
import { useLibrary } from '../store/useLibrary'
import type { ViewId } from '../store/useLibrary'

const TITLES: Record<ViewId, string> = {
  'listen-now': 'Listen Now',
  albums: 'Albums',
  artists: 'Artists',
  playlists: 'Playlists',
  'album-detail': 'Album',
}

export function AppShell() {
  const {
    albums, total, truncated, loading, online, query, view, playback,
    setQuery, setView, selectAlbum, playTrack, togglePlay, seek, load, loadMore, selectedAlbum, albumTracks,
  } = useLibrary()

  const needle = query.trim().toLowerCase()
  const visible = needle
    ? albums.filter(
        (album) =>
          album.name.toLowerCase().includes(needle) ||
          album.artists.some((artist) => artist.toLowerCase().includes(needle))
      )
    : albums

  const isDetail = view === 'album-detail'

  return (
    <div className="shell">
      <Sidebar view={isDetail ? 'albums' : view} onSelect={setView} />

      <div className="shell__main">
        <TopBar
          title={isDetail ? (selectedAlbum?.name ?? 'Album') : TITLES[view]}
          query={query}
          online={online}
          loading={loading}
          onQuery={setQuery}
        />

        <main className="content">
          {isDetail ? (
            <AlbumDetailPage
              album={selectedAlbum}
              tracks={albumTracks}
              onBack={() => setView('albums')}
              onPlayTrack={playTrack}
              onPlayAlbum={() => selectedAlbum && playTrack(selectedAlbum.id)}
              playingTrackId={playback.trackId}
            />
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

        <PlayerBar playback={playback} onToggle={togglePlay} onSeek={seek} />
      </div>
    </div>
  )
}