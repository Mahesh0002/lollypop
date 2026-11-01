import { motion } from 'framer-motion'
import { Play, Shuffle, Heart, MoreHorizontal, ChevronLeft } from 'lucide-react'
import type { Album, Track } from '../ipc/bridge'
import { TrackList } from './TrackList'
import { formatTime, initials } from '../lib/format'

type Props = {
  album: Album | null
  tracks: Track[]
  onBack: () => void
  onPlayTrack: (trackId: number) => void
  onPlayAlbum: () => void
  playingTrackId?: number | null
}

export function AlbumDetailPage({ album, tracks, onBack, onPlayTrack, onPlayAlbum, playingTrackId }: Props) {
  if (!album) return null

  const hasCover = album.cover_path && album.cover_path !== ''
  const duration = tracks.reduce((sum, t) => sum + (t.duration || 0), 0)
  const trackCount = tracks.length

  return (
    <motion.div className="detail" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.3 }}>
      <div className="detail__hero">
        <button type="button" className="iconbtn" onClick={onBack} aria-label="Back">
          <ChevronLeft size={24} />
        </button>

        <div className="detail__art" aria-hidden="true">
          {hasCover && album.cover_path ? (
            <img src={album.cover_path} alt="" loading="lazy" />
          ) : (
            <span>{initials(album.name)}</span>
          )}
        </div>

        <div className="detail__meta">
          <div className="detail__meta-row">
            <span>{trackCount} {trackCount === 1 ? 'song' : 'songs'}</span>
            <span>{formatTime(duration)}</span>
            {album.year && <span>{album.year}</span>}
          </div>

          <h1 className="detail__title">{album.name}</h1>
          <p className="detail__artist">{album.artists.join(', ')}</p>

          <div className="detail__actions">
            <button
              type="button"
              className="btn"
              onClick={onPlayAlbum}
              disabled={tracks.length === 0}
            >
              <Play size={18} fill="currentColor" strokeWidth={0} />
              Play
            </button>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => { if (tracks.length) onPlayTrack(tracks[0].id); }}
              disabled={tracks.length === 0}
            >
              <Shuffle size={18} />
              Shuffle
            </button>
            <button type="button" className="btn btn--ghost" aria-label="Add to library">
              <Heart size={18} />
            </button>
            <button type="button" className="btn btn--ghost" aria-label="More">
              <MoreHorizontal size={18} />
            </button>
          </div>
        </div>
      </div>

      <div className="detail__tracks">
        <div className="detail__tracks-head">
          <h2 className="detail__tracks-title">Tracks</h2>
        </div>
        <TrackList
          tracks={tracks}
          onPlay={onPlayTrack}
          playingTrackId={playingTrackId}
        />
      </div>
    </motion.div>
  )
}