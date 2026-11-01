import { Disc3, Library, ListMusic, Radio, Users } from 'lucide-react'
import type { ComponentType } from 'react'
import type { ViewId } from '../store/useLibrary'

type Item = { id: ViewId; label: string; icon: ComponentType<{ size?: number }> }

const BROWSE: Item[] = [
  { id: 'listen-now', label: 'Listen Now', icon: Disc3 },
  { id: 'albums', label: 'Albums', icon: Library },
  { id: 'artists', label: 'Artists', icon: Users },
  { id: 'playlists', label: 'Playlists', icon: ListMusic },
]

const LIBRARY: Item[] = [
  { id: 'albums', label: 'Recently Added', icon: Radio },
]

type Props = {
  view: ViewId
  onSelect: (view: ViewId) => void
}

export function Sidebar({ view, onSelect }: Props) {
  return (
    <nav className="shell__sidebar" aria-label="Library">
      <div className="sidebar__group">
        <div className="sidebar__label">Browse</div>
        {BROWSE.map((item) => (
          <button
            key={item.id}
            type="button"
            className="sidebar__item"
            aria-current={view === item.id}
            onClick={() => onSelect(item.id)}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </div>

      <div className="sidebar__group">
        <div className="sidebar__label">Library</div>
        {LIBRARY.map((item) => (
          <button
            key={item.label}
            type="button"
            className="sidebar__item"
            aria-current="false"
            onClick={() => onSelect(item.id)}
          >
            <item.icon size={16} />
            {item.label}
          </button>
        ))}
      </div>
    </nav>
  )
}