import { Search } from 'lucide-react'

type Props = {
  title: string
  query: string
  online: boolean
  loading: boolean
  onQuery: (query: string) => void
}

export function TopBar({ title, query, online, loading, onQuery }: Props) {
  return (
    <header className="topbar">
      <span className="topbar__title">{title}</span>
      <span className="topbar__spacer" />

      <label className="search">
        <Search size={15} aria-hidden="true" />
        <input
          type="search"
          placeholder="Search"
          aria-label="Search library"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
        />
      </label>

      <span
        className={`status${online ? ' status--online' : ''}`}
        title={online ? 'Connected to Lollypop' : 'Running without the host'}
      >
        <span className="status__dot" />
        {loading ? 'loading' : online ? 'connected' : 'offline'}
      </span>
    </header>
  )
}