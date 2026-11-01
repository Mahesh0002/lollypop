import { motion, AnimatePresence } from 'framer-motion'
import { Heart, ListMusic, Volume2, Mic, ChevronUp } from 'lucide-react'
import type { Track } from '../ipc/bridge'
import { formatTime, initials } from '../lib/format'

type Props = {
  isOpen: boolean
  onClose: () => void
  playback: {
    playing: boolean
    trackId: number | null
    title: string | null
    artist: string | null
    position: number
    duration: number
    volume: number
  }
  albumTracks: Track[]
  onPlayTrack: (trackId: number) => void
  onTogglePlay: () => void
  onSeek: (position: number) => void
  onSetVolume: (volume: number) => void
}

export function MiniPlayer({
  isOpen,
  onClose,
  playback,
  albumTracks,
  onPlayTrack,
  onTogglePlay,
  onSeek,
  onSetVolume,
}: Props) {
  const { playing, title, artist, position, duration, volume } = playback
  const percent = duration > 0 ? Math.min(100, (position / duration) * 100) : 0
  const hasTrack = title !== null
  const coverArt = hasTrack ? initials(title!) : '♪'

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* Backdrop */}
          <motion.div
            className="miniplayer__backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
          />

          {/* Mini Player Sheet */}
          <motion.div
            className="miniplayer"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', damping: 25, stiffness: 200 }}
          >
            <div className="miniplayer__handle" onClick={onClose}>
              <ChevronUp size={20} className="miniplayer__chevron" />
            </div>

            <div className="miniplayer__content">
              {/* Large Artwork */}
              <div className="miniplayer__art" aria-hidden="true">
                <span>{coverArt}</span>
              </div>

              {/* Track Info */}
              <div className="miniplayer__meta">
                <h2 className="miniplayer__title">{title ?? 'Not Playing'}</h2>
                <p className="miniplayer__artist">{artist ?? '—'}</p>
              </div>

              {/* Actions */}
              <div className="miniplayer__actions">
                <button
                  type="button"
                  className="iconbtn miniplayer__action"
                  aria-label="Add to library"
                  disabled={!hasTrack}
                >
                  <Heart size={20} />
                </button>
                <button
                  type="button"
                  className="iconbtn miniplayer__action"
                  aria-label="Queue"
                  disabled={!hasTrack}
                >
                  <ListMusic size={20} />
                </button>
                <button
                  type="button"
                  className="iconbtn miniplayer__action"
                  aria-label="Lyrics"
                  disabled={!hasTrack}
                >
                  <Mic size={20} />
                </button>
              </div>

              {/* Progress Bar */}
              <div className="miniplayer__progress">
                <span className="miniplayer__time">{formatTime(position)}</span>
                <div
                  className={`progress${playing ? ' progress--live' : ''}`}
                  role="slider"
                  aria-label="Playback position"
                  aria-valuemin={0}
                  aria-valuemax={duration || 0}
                  aria-valuenow={position}
                  onClick={(event) => {
                    if (duration <= 0) return
                    const rect = event.currentTarget.getBoundingClientRect()
                    const ratio = (event.clientX - rect.left) / rect.width
                    onSeek(Math.max(0, Math.min(duration, ratio * duration)))
                  }}
                >
                  <div className="progress__fill" style={{ width: `${percent}%` }} />
                </div>
                <span className="miniplayer__time">{formatTime(duration)}</span>
              </div>

              {/* Main Controls */}
              <div className="miniplayer__controls">
                <button
                  type="button"
                  className="iconbtn miniplayer__control"
                  aria-label="Shuffle"
                >
                  <span className="miniplayer__icon">🔀</span>
                </button>
                <button
                  type="button"
                  className="iconbtn miniplayer__control"
                  aria-label="Previous"
                >
                  <span className="miniplayer__icon">⏮</span>
                </button>
                <button
                  type="button"
                  className={`iconbtn miniplayer__play${playing ? ' miniplayer__play--playing' : ''}`}
                  aria-label={playing ? 'Pause' : 'Play'}
                  onClick={onTogglePlay}
                  disabled={!hasTrack}
                >
                  {playing ? <span className="miniplayer__icon">⏸</span> : <span className="miniplayer__icon">▶</span>}
                </button>
                <button
                  type="button"
                  className="iconbtn miniplayer__control"
                  aria-label="Next"
                >
                  <span className="miniplayer__icon">⏭</span>
                </button>
                <button
                  type="button"
                  className="iconbtn miniplayer__control"
                  aria-label="Repeat"
                >
                  <span className="miniplayer__icon">🔁</span>
                </button>
              </div>

              {/* Volume */}
              <div className="miniplayer__volume">
                <Volume2 size={18} className="miniplayer__volume-icon" />
                <input
                  type="range"
                  className="miniplayer__volume-slider"
                  min={0}
                  max={1}
                  step={0.01}
                  value={volume}
                  onChange={(e) => onSetVolume(Number(e.target.value))}
                  aria-label="Volume"
                />
              </div>

              {/* Queue / Up Next */}
              {albumTracks.length > 0 && (
                <div className="miniplayer__queue">
                  <h3 className="miniplayer__queue-title">Up Next</h3>
                  <div className="miniplayer__queue-list" role="list">
                    {albumTracks.slice(0, 10).map((track, index) => (
                      <button
                        key={track.id}
                        type="button"
                        className={`miniplayer__queue-item${playback.trackId === track.id ? ' miniplayer__queue-item--playing' : ''}`}
                        onClick={() => onPlayTrack(track.id)}
                      >
                        <span className="miniplayer__queue-num">
                          {playback.trackId === track.id ? '▶' : index + 1}
                        </span>
                        <div className="miniplayer__queue-meta">
                          <span className="miniplayer__queue-title">{track.name}</span>
                          <span className="miniplayer__queue-artist">{track.artists.join(', ')}</span>
                        </div>
                        <span className="miniplayer__queue-duration">{formatTime(track.duration)}</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  )
}