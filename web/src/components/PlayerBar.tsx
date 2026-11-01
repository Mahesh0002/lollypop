import { Heart, Pause, Play, Repeat, Shuffle, SkipBack, SkipForward, Volume2 } from 'lucide-react'
import { formatTime, initials } from '../lib/format'
import type { Playback } from '../store/useLibrary'

type Props = {
  playback: Playback
  onToggle: () => void
  onSeek: (position: number) => void
}

export function PlayerBar({ playback, onToggle, onSeek }: Props) {
  const { playing, title, artist, position, duration } = playback
  const percent = duration > 0 ? Math.min(100, (position / duration) * 100) : 0
  const hasTrack = title !== null

  return (
    <footer className="shell__player">
      <div className="player">
        <div className="player__now">
          <div className="player__art" aria-hidden="true">
            {hasTrack ? initials(title!) : '♪'}
          </div>
          <div className="player__meta">
            <div className="player__title">{title ?? 'Not Playing'}</div>
            <div className="player__artist">{artist ?? '—'}</div>
          </div>
          <button type="button" className="iconbtn" aria-label="Love" disabled={!hasTrack}>
            <Heart size={16} />
          </button>
        </div>

        <div className="player__center">
          <div className="player__controls">
            <button type="button" className="iconbtn" aria-label="Shuffle">
              <Shuffle size={16} />
            </button>
            <button type="button" className="iconbtn" aria-label="Previous">
              <SkipBack size={18} fill="currentColor" strokeWidth={0} />
            </button>
            <button
              type="button"
              className="iconbtn player__play"
              aria-label={playing ? 'Pause' : 'Play'}
              onClick={onToggle}
              disabled={!hasTrack}
            >
              {playing
                ? <Pause size={16} fill="currentColor" strokeWidth={0} />
                : <Play size={16} fill="currentColor" strokeWidth={0} />}
            </button>
            <button type="button" className="iconbtn" aria-label="Next">
              <SkipForward size={18} fill="currentColor" strokeWidth={0} />
            </button>
            <button type="button" className="iconbtn" aria-label="Repeat">
              <Repeat size={16} />
            </button>
          </div>

          <div className="player__scrub">
            <span>{formatTime(position)}</span>
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
            <span>{formatTime(duration)}</span>
          </div>
        </div>

        <div className="player__right">
          <button type="button" className="iconbtn" aria-label="Volume">
            <Volume2 size={16} />
          </button>
        </div>
      </div>
    </footer>
  )
}