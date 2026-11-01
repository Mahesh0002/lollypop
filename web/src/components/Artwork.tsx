import { Play } from 'lucide-react'
import type { Album } from '../ipc/bridge'
import { initials } from '../lib/format'

type Props = {
  album: Album
  onPlay?: (albumId: number) => void
}

/**
 * Square artwork tile. Phase 2 swaps the gradient placeholder for real
 * cover art; hover scale + play affordance are already final.
 */
export function Artwork({ album, onPlay }: Props) {
  return (
    <div className="art">
      <span aria-hidden="true">{initials(album.name)}</span>
      {onPlay && (
        <button
          type="button"
          className="art__play"
          aria-label={`Play ${album.name}`}
          onClick={(event) => {
            event.stopPropagation()
            onPlay(album.id)
          }}
        >
          <Play size={18} fill="currentColor" strokeWidth={0} />
        </button>
      )}
    </div>
  )
}