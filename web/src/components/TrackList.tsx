import { motion } from 'framer-motion'
import { Play, Pause, Music } from 'lucide-react'
import type { Track } from '../ipc/bridge'
import { formatTime } from '../lib/format'

type Props = {
  tracks: Track[]
  onPlay: (trackId: number) => void
  playingTrackId?: number | null
}

const EASE = [0.16, 1, 0.3, 1] as [number, number, number, number]

const rowVariants = {
  hidden: { opacity: 0, x: -20 },
  show: { opacity: 1, x: 0, transition: { duration: 0.2, ease: EASE } },
}

export function TrackList({ tracks, onPlay, playingTrackId }: Props) {
  return (
    <div className="tracklist" role="list" aria-label="Tracks">
      {tracks.map((track, index) => (
        <motion.button
          key={track.id}
          type="button"
          className={`tracklist__row${playingTrackId === track.id ? ' tracklist__row--playing' : ''}`}
          variants={rowVariants}
          initial="hidden"
          animate="show"
          style={{ transitionDelay: `${index * 0.03}s` }}
          onClick={() => onPlay(track.id)}
        >
          <div className="tracklist__num">
            {playingTrackId === track.id ? (
              <Pause size={14} fill="currentColor" strokeWidth={0} className="tracklist__playing" />
            ) : (
              <span>{track.track_number ?? index + 1}</span>
            )}
          </div>

          <div className="tracklist__meta">
            <div className="tracklist__title">{track.name}</div>
            <div className="tracklist__artists">{track.artists.join(', ')}</div>
          </div>

          <div className="tracklist__duration" aria-hidden="true">
            {formatTime(track.duration)}
          </div>

          <button
            type="button"
            className="tracklist__play"
            aria-label={`Play ${track.name}`}
            onClick={(e) => { e.stopPropagation(); onPlay(track.id); }}
          >
            <Play size={16} fill="currentColor" strokeWidth={0} />
          </button>
        </motion.button>
      ))}
      {tracks.length === 0 && (
        <div className="empty" style={{ padding: 'var(--space-8)', textAlign: 'center' }}>
          <Music size={32} style={{ color: 'var(--color-fg-tertiary)', marginBottom: 'var(--space-3)' }} />
          <p style={{ color: 'var(--color-fg-secondary)' }}>No tracks</p>
        </div>
      )}
    </div>
  )
}