import { motion } from 'framer-motion'
import { Play, ChevronLeft } from 'lucide-react'
import type { Track } from '../ipc/bridge'
import { TrackList } from './TrackList'

type Props = {
  tracks: Track[]
  onPlay: (trackId: number) => void
  onBack: () => void
  playingTrackId?: number | null
}

export function TracksView({ tracks, onPlay, onBack, playingTrackId }: Props) {
  return (
    <motion.div
      className="detail"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
    >
      <div className="detail__hero">
        <button type="button" className="iconbtn" onClick={onBack} aria-label="Back">
          <ChevronLeft size={24} />
        </button>

        <div className="detail__art" aria-hidden="true">
          <span>🎵</span>
        </div>

        <div className="detail__meta">
          <h1 className="detail__title">Songs</h1>
          <p className="detail__artist">{tracks.length} tracks</p>
          <div className="detail__actions">
            <button
              type="button"
              className="btn"
              onClick={() => tracks.length && onPlay(tracks[0].id)}
              disabled={tracks.length === 0}
            >
              <Play size={18} fill="currentColor" strokeWidth={0} />
              Play
            </button>
            <button type="button" className="btn btn--ghost">
              Shuffle
            </button>
          </div>
        </div>
      </div>

      <div className="detail__tracks">
        <div className="detail__tracks-head">
          <h2 className="detail__tracks-title">All Songs</h2>
        </div>
        <TrackList
          tracks={tracks}
          onPlay={onPlay}
          playingTrackId={playingTrackId}
        />
      </div>
    </motion.div>
  )
}