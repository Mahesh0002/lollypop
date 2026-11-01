import { motion } from 'framer-motion'
import type { Album } from '../ipc/bridge'
import { Artwork } from './Artwork'

type Props = {
  albums: Album[]
  onPlay: (albumId: number) => void
  onSelect?: (album: Album) => void
}

const stagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.02 } },
}

const EASE = [0.16, 1, 0.3, 1] as [number, number, number, number]

const item = {
  hidden: { opacity: 0, y: 8 },
  show: { opacity: 1, y: 0, transition: { duration: 0.3, ease: EASE } },
}

export function AlbumGrid({ albums, onPlay, onSelect }: Props) {
  return (
    <motion.div
      className="grid"
      variants={stagger}
      initial="hidden"
      animate="show"
    >
      {albums.map((album) => (
        <motion.button
          key={album.id}
          type="button"
          className="card"
          variants={item}
          onClick={() => onSelect?.(album)}
        >
          <Artwork album={album} onPlay={onPlay} />
          <div className="card__title">{album.name}</div>
          <div className="card__subtitle">
            {album.artists.length > 0 ? album.artists.join(', ') : 'Unknown artist'}
          </div>
        </motion.button>
      ))}
    </motion.div>
  )
}