import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { PlayerBar } from './PlayerBar'
import { AlbumGrid } from './AlbumGrid'
import { useLibrary } from '../store/useLibrary'
import type { ViewId } from '../store/useLibrary'

const TITLES: Record<ViewId, string> = {
  'listen-now': 'Listen Now',
  albums: 'Albums',
  artists: 'Artists',
  playlists: 'Playlists',
}

export function AppShell() {
  const {
    albums, total, truncated, loading, online, query, view, playback,
    setQuery, setView, playAlbum, togglePlay, seek, load,
  } = useLibrary()

  const needle = query.trim().toLowerCase()
  const visible = needle
    ? albums.filter(
        (album) =>
          album.name.toLowerCase().includes(needle) ||
          album.artists.some((artist) => artist.toLowerCase().includes(needle))
      )
    : albums

  return (
    <div className="shell">
      <Sidebar view={view} onSelect={setView} />

      <div className="shell__main">
        <TopBar
          title={TITLES[view]}
          query={query}
          online={online}
          loading={loading}
          onQuery={setQuery}
        />

        <main className="content">
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
            <AlbumGrid albums={visible} onPlay={playAlbum} />
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
                  <button type="button" className="btn btn--ghost" onClick={load}>
                    Retry connection
                  </button>
                </p>
              )}
            </div>
          )}
        </main>
      </div>

      <PlayerBar playback={playback} onToggle={togglePlay} onSeek={seek} />
    </div>
  )
}