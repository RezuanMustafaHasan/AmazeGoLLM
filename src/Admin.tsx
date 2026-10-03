import { useCallback, useEffect, useState } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleUserRound,
  Clock3,
  Eye,
  Layers3,
  LoaderCircle,
  LogOut,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
  Bot,
  Activity,
} from 'lucide-react'
import type { LevelProgress } from './types'
import Agents from './Agents'
import ApiHealth from './ApiHealth'
import './admin.css'

type Screen = 'overview' | 'players' | 'sessions' | 'agents' | 'api-health'
interface Auth {
  username: string
  csrf_token: string
}
interface PlayerRecord {
  id: string
  created_at: string
  active_session_id: string | null
  progress: Record<string, LevelProgress>
}
interface SessionRecord {
  id: string
  player_id: string
  level_id: string
  actor_type: string
  actor_name: string | null
  status: string
  lives_remaining: number
  moves: number
  mistakes: number
  hints_used: number
  revision: number
  created_at: string
  updated_at: string
  completed_at: string | null
  action_count: number
}
interface Action {
  action_id: string
  type: string
  arrow_id: number
  result: string
  blocked_by: number | null
  reward: number
  revision: number
  at: string
}
interface SessionDetail extends SessionRecord {
  history: Action[]
  removed_ids: number[]
}
interface Page<T> {
  items: T[]
  next_cursor: string | null
}
interface Stats {
  players: number
  sessions: number
  levels: number
  statuses: Record<string, number>
}
interface DeleteTarget {
  type: 'player' | 'session'
  id: string
}
const short = (id: string) => id.slice(0, 8)
const date = (value: string | null) => (value ? new Date(value).toLocaleString() : '—')
const levelNumber = (id: string) => Number(id.replace('level-', '')) || id
const message = (error: unknown) =>
  error instanceof Error ? error.message : 'The request failed. Please retry.'

export default function Admin() {
  const [auth, setAuth] = useState<Auth | null>(null)
  const [checking, setChecking] = useState(true)
  const [username, setUsername] = useState('admin')
  const [password, setPassword] = useState('')
  const [screen, setScreen] = useState<Screen>('overview')
  const [agentsOpened, setAgentsOpened] = useState(false)
  const [stats, setStats] = useState<Stats | null>(null)
  const [players, setPlayers] = useState<Page<PlayerRecord>>({ items: [], next_cursor: null })
  const [sessions, setSessions] = useState<Page<SessionRecord>>({ items: [], next_cursor: null })
  const [playerFilter, setPlayerFilter] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [detail, setDetail] = useState<
    { type: 'player'; data: PlayerRecord } | { type: 'session'; data: SessionDetail } | null
  >(null)
  const [target, setTarget] = useState<DeleteTarget | null>(null)

  const request = useCallback(
    async <T,>(path: string, options: RequestInit = {}): Promise<T> => {
      const response = await fetch(`/api/admin${path}`, {
        ...options,
        credentials: 'same-origin',
        signal: options.signal ?? AbortSignal.timeout(30000),
        headers: {
          'Content-Type': 'application/json',
          ...(auth ? { 'X-Admin-CSRF': auth.csrf_token } : {}),
          ...options.headers,
        },
      })
      if (!response.ok) {
        const data = await response.json().catch(() => ({}))
        if (response.status === 401 && path !== '/login') setAuth(null)
        throw new Error(data.detail || 'The request failed. Please retry.')
      }
      return response.json() as Promise<T>
    },
    [auth],
  )

  useEffect(() => {
    let cancelled = false
    fetch('/api/admin/auth', { credentials: 'same-origin' })
      .then(async (response) => {
        if (response.ok) {
          const data = (await response.json()) as Auth
          if (!cancelled) setAuth(data)
        }
      })
      .catch(() => {
        if (!cancelled) setError('The server is unavailable. Please try again.')
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const reload = useCallback(async () => {
    setBusy(true)
    setError(null)
    try {
      const [summary, people, attempts] = await Promise.all([
        request<Stats>('/stats'),
        request<Page<PlayerRecord>>('/players'),
        request<Page<SessionRecord>>(
          `/sessions${playerFilter ? `?player_id=${playerFilter}` : ''}`,
        ),
      ])
      setStats(summary)
      setPlayers(people)
      setSessions(attempts)
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }, [request, playerFilter])

  useEffect(() => {
    if (auth) void reload()
  }, [auth, reload])
  useEffect(() => {
    window.scrollTo(0, 0)
  }, [auth, screen])
  useEffect(() => {
    if (!detail && !target) return
    const previous = document.activeElement as HTMLElement | null
    const dialog = document.querySelector<HTMLElement>('.admin-overlay section')
    const focusable = () =>
      Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled), a, input') || [])
    focusable()[0]?.focus()
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !busy) {
        setDetail(null)
        setTarget(null)
      }
      if (event.key === 'Tab') {
        const elements = focusable()
        const first = elements[0]
        const last = elements[elements.length - 1]
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault()
          last?.focus()
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault()
          first?.focus()
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      previous?.focus()
    }
  }, [detail, target, busy])

  async function signIn(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const signedIn = await request<Auth>('/login', {
        method: 'POST',
        body: JSON.stringify({ username, password }),
      })
      setPassword('')
      setAuth(signedIn)
    } catch (err) {
      setPassword('')
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  async function signOut() {
    setBusy(true)
    setError(null)
    try {
      await request('/logout', { method: 'POST' })
      setAuth(null)
      setDetail(null)
      setTarget(null)
      setStats(null)
      setPlayers({ items: [], next_cursor: null })
      setSessions({ items: [], next_cursor: null })
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  async function inspect(type: 'player' | 'session', id: string) {
    setBusy(true)
    setError(null)
    try {
      if (type === 'player')
        setDetail({ type, data: await request<PlayerRecord>(`/players/${id}`) })
      else setDetail({ type, data: await request<SessionDetail>(`/sessions/${id}`) })
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  async function remove() {
    if (!target) return
    setBusy(true)
    setError(null)
    try {
      const result = await request<{ deleted_sessions: number }>(
        `/${target.type === 'player' ? 'players' : 'sessions'}/${target.id}`,
        { method: 'DELETE' },
      )
      setNotice(
        target.type === 'player'
          ? `Player deleted with ${result.deleted_sessions} associated sessions.`
          : 'Session deleted. Player progress has been updated.',
      )
      setTarget(null)
      setDetail(null)
      if (target.type === 'player' && playerFilter === target.id) setPlayerFilter(null)
      await reload()
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  async function loadMore(type: 'players' | 'sessions') {
    const cursor = type === 'players' ? players.next_cursor : sessions.next_cursor
    if (!cursor) return
    setBusy(true)
    setError(null)
    try {
      const suffix = type === 'sessions' && playerFilter ? `&player_id=${playerFilter}` : ''
      if (type === 'players') {
        const next = await request<Page<PlayerRecord>>(`/players?cursor=${cursor}`)
        setPlayers((previous) => ({
          items: [...previous.items, ...next.items],
          next_cursor: next.next_cursor,
        }))
      } else {
        const next = await request<Page<SessionRecord>>(`/sessions?cursor=${cursor}${suffix}`)
        setSessions((previous) => ({
          items: [...previous.items, ...next.items],
          next_cursor: next.next_cursor,
        }))
      }
    } catch (err) {
      setError(message(err))
    } finally {
      setBusy(false)
    }
  }
  function navigate(next: Screen) {
    if (next === 'agents') setAgentsOpened(true)
    setScreen(next)
    setSearch('')
    setNotice(null)
    if (next !== 'sessions') setPlayerFilter(null)
  }

  if (checking)
    return (
      <div className="admin-loading">
        <LoaderCircle className="admin-spin" /> Loading administration…
      </div>
    )
  if (!auth)
    return (
      <div className="admin-login-page">
        <a className="admin-back" href="/">
          <ArrowLeft size={16} /> Back to the game
        </a>
        <form className="admin-login" onSubmit={(event) => void signIn(event)}>
          <div className="admin-login-icon">
            <ShieldCheck size={27} />
          </div>
          <p className="admin-eyebrow">AMAZE GO · ADMINISTRATION</p>
          <h1>Welcome back.</h1>
          <p className="admin-muted">Sign in to manage players and game sessions.</p>
          <label htmlFor="admin-username">Username</label>
          <input
            id="admin-username"
            autoComplete="username"
            value={username}
            onChange={(event) => setUsername(event.target.value)}
            required
            maxLength={128}
          />
          <label htmlFor="admin-password">Password</label>
          <input
            id="admin-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            maxLength={256}
          />
          {error && (
            <div className="admin-error" role="alert">
              {error}
            </div>
          )}
          <button className="admin-primary" disabled={busy}>
            {busy ? <LoaderCircle className="admin-spin" size={17} /> : 'Sign in'}{' '}
            <ArrowRight size={17} />
          </button>
          <p className="admin-login-note">
            <ShieldCheck size={13} /> Administrator access only
          </p>
        </form>
      </div>
    )

  const visiblePlayers = players.items.filter((player) =>
    player.id.toLowerCase().includes(search.toLowerCase()),
  )
  const visibleSessions = sessions.items.filter((session) =>
    [
      session.id,
      session.player_id,
      session.level_id,
      session.actor_name || '',
      session.status,
    ].some((value) => value.toLowerCase().includes(search.toLowerCase())),
  )
  const sessionTable = (items: SessionRecord[]) => (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Session</th>
            <th>Player</th>
            <th>Level</th>
            <th>Status</th>
            <th>Moves / mistakes</th>
            <th>Started</th>
            <th>
              <span className="admin-sr-only">Actions</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {items.map((session) => (
            <tr key={session.id}>
              <td>
                <button className="admin-id" onClick={() => void inspect('session', session.id)}>
                  {short(session.id)}
                  <ChevronRight size={13} />
                </button>
                <small>
                  {session.actor_type}
                  {session.actor_name ? ` · ${session.actor_name}` : ''}
                </small>
              </td>
              <td>
                <button
                  className="admin-id"
                  onClick={() => void inspect('player', session.player_id)}
                >
                  {short(session.player_id)}
                </button>
              </td>
              <td>{levelNumber(session.level_id)}</td>
              <td>
                <span className={`admin-status ${session.status}`}>{session.status}</span>
              </td>
              <td>
                {session.moves}
                <span className="admin-muted"> / {session.mistakes}</span>
              </td>
              <td className="admin-date">{date(session.created_at)}</td>
              <td>
                <div className="admin-actions">
                  <button
                    aria-label={`View session ${session.id}`}
                    onClick={() => void inspect('session', session.id)}
                    disabled={busy}
                  >
                    <Eye size={16} />
                  </button>
                  <button
                    className="admin-danger-icon"
                    aria-label={`Delete session ${session.id}`}
                    onClick={() => setTarget({ type: 'session', id: session.id })}
                    disabled={busy}
                  >
                    <Trash2 size={16} />
                  </button>
                </div>
              </td>
            </tr>
          ))}
          {!items.length && (
            <tr>
              <td colSpan={7} className="admin-empty">
                {busy ? 'Loading sessions…' : 'No sessions found.'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <a className="admin-brand" href="/">
          <span>↗</span> Amaze Go
        </a>
        <p className="admin-eyebrow">WORKSPACE</p>
        <nav aria-label="Admin navigation">
          <button
            className={screen === 'agents' ? 'selected' : ''}
            onClick={() => navigate('agents')}
          >
            <Bot size={18} /> LLM agents
          </button>
          <button
            className={screen === 'api-health' ? 'selected' : ''}
            onClick={() => navigate('api-health')}
          >
            <Activity size={18} /> API health
          </button>
          <button
            className={screen === 'overview' ? 'selected' : ''}
            onClick={() => navigate('overview')}
          >
            <Layers3 size={18} />
            Overview
          </button>
          <button
            className={screen === 'players' ? 'selected' : ''}
            onClick={() => navigate('players')}
          >
            <CircleUserRound size={18} />
            Players <span>{stats?.players ?? '—'}</span>
          </button>
          <button
            className={screen === 'sessions' ? 'selected' : ''}
            onClick={() => navigate('sessions')}
          >
            <Clock3 size={18} />
            Sessions <span>{stats?.sessions ?? '—'}</span>
          </button>
        </nav>
        <div className="admin-sidebar-bottom">
          <a href="/">
            <ArrowLeft size={16} /> Back to game
          </a>
          <div className="admin-identity">
            <ShieldCheck size={18} />
            <div>
              <strong>{auth.username}</strong>
              <small>Administrator</small>
            </div>
            <button onClick={() => void signOut()} disabled={busy} aria-label="Sign out">
              <LogOut size={17} />
            </button>
          </div>
        </div>
      </aside>
      <main className="admin-main">
        <header className="admin-topbar">
          <span>
            Administration <ChevronRight size={14} />{' '}
            {screen === 'api-health' ? 'API health' : screen[0].toUpperCase() + screen.slice(1)}
          </span>
          <span className="admin-connection">
            <i /> Connected
          </span>
        </header>
        <section className="admin-content">
          <div className="admin-heading">
            <div>
              <p className="admin-eyebrow">AMAZE GO</p>
              <h1>
                {screen === 'overview'
                  ? 'Your game, at a glance.'
                  : screen === 'players'
                    ? 'Players'
                    : screen === 'agents'
                      ? 'LLM agents'
                      : screen === 'api-health'
                        ? 'API health'
                        : 'Game sessions'}
              </h1>
              <p className="admin-muted">
                {screen === 'overview'
                  ? 'A live view of player activity and saved attempts.'
                  : screen === 'players'
                    ? 'Inspect progress and manage player records.'
                    : screen === 'agents'
                      ? 'Run visual puzzle evaluations and inspect every decision.'
                      : screen === 'api-health'
                        ? 'Verify your UFL connection with real model responses.'
                        : 'Review attempts, outcomes, and action history.'}
              </p>
            </div>
            <button className="admin-secondary" onClick={() => void reload()} disabled={busy}>
              <RefreshCw size={16} className={busy ? 'admin-spin' : ''} /> Refresh
            </button>
          </div>
          {error && (
            <div role="alert" className="admin-error">
              {error}
            </div>
          )}
          {notice && (
            <div role="status" className="admin-notice">
              <CheckCircle2 size={17} />
              {notice}
              <button aria-label="Dismiss notification" onClick={() => setNotice(null)}>
                <X size={15} />
              </button>
            </div>
          )}
          {screen === 'overview' && (
            <>
              <div className="admin-metrics">
                {[
                  {
                    label: 'Players',
                    value: stats?.players,
                    icon: CircleUserRound,
                    text: 'Anonymous player identities',
                  },
                  {
                    label: 'Sessions',
                    value: stats?.sessions,
                    icon: Layers3,
                    text: 'Saved game attempts',
                  },
                  {
                    label: 'Active attempts',
                    value: stats?.statuses.active || 0,
                    icon: Clock3,
                    text: 'Available to resume',
                  },
                  {
                    label: 'Completed wins',
                    value: stats?.statuses.won || 0,
                    icon: CheckCircle2,
                    text: 'Successfully cleared boards',
                  },
                ].map((metric) => (
                  <article key={metric.label}>
                    <div>
                      <span>{metric.label}</span>
                      <metric.icon size={18} />
                    </div>
                    <strong>{metric.value ?? '—'}</strong>
                    <small>{metric.text}</small>
                  </article>
                ))}
              </div>
              <div className="admin-card">
                <div className="admin-card-heading">
                  <div>
                    <h2>Saved sessions</h2>
                    <p>Open an attempt to view its complete action history.</p>
                  </div>
                  <button className="admin-link" onClick={() => navigate('sessions')}>
                    View all <ArrowRight size={15} />
                  </button>
                </div>
                {sessionTable(sessions.items.slice(0, 8))}
              </div>
              <p className="admin-footnote">
                {stats?.levels ?? '—'} levels available from the repository catalog.
              </p>
            </>
          )}
          {agentsOpened && (
            <div hidden={screen !== 'agents'}>
              <Agents request={request} />
            </div>
          )}
          {screen === 'api-health' && <ApiHealth request={request} />}
          {(screen === 'players' || screen === 'sessions') && (
            <div className="admin-card">
              <div className="admin-list-toolbar">
                <div className="admin-search">
                  <Search size={16} />
                  <input
                    aria-label={`Search loaded ${screen}`}
                    placeholder={
                      screen === 'players'
                        ? 'Search loaded players by ID…'
                        : 'Search loaded sessions, players, levels…'
                    }
                    value={search}
                    onChange={(event) => setSearch(event.target.value)}
                  />
                </div>
                <span className="admin-muted">
                  {screen === 'players' ? players.items.length : sessions.items.length} loaded
                </span>
              </div>
              {playerFilter && (
                <div className="admin-filter">
                  Sessions for player <code>{short(playerFilter)}</code>
                  <button onClick={() => setPlayerFilter(null)}>
                    Clear filter <X size={14} />
                  </button>
                </div>
              )}
              {screen === 'players' ? (
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>Created</th>
                        <th>Levels cleared</th>
                        <th>Active session</th>
                        <th>
                          <span className="admin-sr-only">Actions</span>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {visiblePlayers.map((player) => (
                        <tr key={player.id}>
                          <td>
                            <button
                              className="admin-id"
                              onClick={() => void inspect('player', player.id)}
                            >
                              {short(player.id)}
                              <ChevronRight size={13} />
                            </button>
                          </td>
                          <td className="admin-date">{date(player.created_at)}</td>
                          <td>{Object.keys(player.progress).length}</td>
                          <td>
                            {player.active_session_id ? (
                              <button
                                className="admin-id"
                                onClick={() => void inspect('session', player.active_session_id!)}
                              >
                                {short(player.active_session_id)}
                              </button>
                            ) : (
                              <span className="admin-muted">None</span>
                            )}
                          </td>
                          <td>
                            <div className="admin-actions">
                              <button
                                aria-label={`View player ${player.id}`}
                                onClick={() => void inspect('player', player.id)}
                                disabled={busy}
                              >
                                <Eye size={16} />
                              </button>
                              <button
                                className="admin-danger-icon"
                                aria-label={`Delete player ${player.id}`}
                                onClick={() => setTarget({ type: 'player', id: player.id })}
                                disabled={busy}
                              >
                                <Trash2 size={16} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      ))}
                      {!visiblePlayers.length && (
                        <tr>
                          <td colSpan={5} className="admin-empty">
                            {busy ? 'Loading players…' : 'No players found.'}
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ) : (
                sessionTable(visibleSessions)
              )}
              {(screen === 'players' ? players.next_cursor : sessions.next_cursor) && (
                <div className="admin-pagination">
                  <button
                    className="admin-secondary"
                    onClick={() => void loadMore(screen as 'players' | 'sessions')}
                    disabled={busy}
                  >
                    Load more <ArrowRight size={15} />
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      </main>
      {detail && (
        <div className="admin-overlay" onClick={() => !busy && setDetail(null)}>
          <section
            role="dialog"
            aria-modal="true"
            aria-label={detail.type === 'player' ? 'Player details' : 'Session details'}
            className="admin-detail"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="admin-detail-heading">
              <div>
                <p className="admin-eyebrow">
                  {detail.type === 'player' ? 'PLAYER RECORD' : 'GAME ATTEMPT'}
                </p>
                <h2>
                  {detail.type === 'player'
                    ? 'Player details'
                    : `Level ${levelNumber(detail.data.level_id)}`}
                </h2>
              </div>
              <button onClick={() => setDetail(null)} aria-label="Close details">
                <X size={20} />
              </button>
            </div>
            <dl className="admin-detail-fields">
              <div>
                <dt>ID</dt>
                <dd>
                  <code>{detail.data.id}</code>
                </dd>
              </div>
              <div>
                <dt>Created</dt>
                <dd>{date(detail.data.created_at)}</dd>
              </div>
              {detail.type === 'player' ? (
                <>
                  <div>
                    <dt>Active session</dt>
                    <dd>{detail.data.active_session_id || 'None'}</dd>
                  </div>
                  <div>
                    <dt>Levels cleared</dt>
                    <dd>{Object.keys(detail.data.progress).length}</dd>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <dt>Player</dt>
                    <dd>
                      <code>{detail.data.player_id}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Actor</dt>
                    <dd>
                      {detail.data.actor_type}
                      {detail.data.actor_name ? ` · ${detail.data.actor_name}` : ''}
                    </dd>
                  </div>
                  <div>
                    <dt>Status</dt>
                    <dd>
                      <span className={`admin-status ${detail.data.status}`}>
                        {detail.data.status}
                      </span>
                    </dd>
                  </div>
                  <div>
                    <dt>Moves / mistakes</dt>
                    <dd>
                      {detail.data.moves} / {detail.data.mistakes}
                    </dd>
                  </div>
                  <div>
                    <dt>Lives / hints used</dt>
                    <dd>
                      {detail.data.lives_remaining} / {detail.data.hints_used}
                    </dd>
                  </div>
                  <div>
                    <dt>Completed</dt>
                    <dd>{date(detail.data.completed_at)}</dd>
                  </div>
                </>
              )}
            </dl>
            {detail.type === 'player' ? (
              <>
                <h3>Level progress</h3>
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Level</th>
                        <th>Stars</th>
                        <th>Best moves</th>
                        <th>Best time</th>
                        <th>Wins</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(detail.data.progress)
                        .sort(([a], [b]) => Number(levelNumber(a)) - Number(levelNumber(b)))
                        .map(([id, progress]) => (
                          <tr key={id}>
                            <td>{levelNumber(id)}</td>
                            <td>{'★'.repeat(progress.stars)}</td>
                            <td>{progress.best_moves}</td>
                            <td>{(progress.best_time_ms / 1000).toFixed(1)}s</td>
                            <td>{progress.completions}</td>
                          </tr>
                        ))}
                      {!Object.keys(detail.data.progress).length && (
                        <tr>
                          <td colSpan={5} className="admin-empty">
                            No completed levels yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
                <button
                  className="admin-secondary admin-view-sessions"
                  onClick={() => {
                    setPlayerFilter(detail.data.id)
                    setScreen('sessions')
                    setSearch('')
                    setDetail(null)
                  }}
                >
                  View this player’s sessions <ArrowRight size={15} />
                </button>
              </>
            ) : (
              <>
                <h3>
                  Action history <span>{detail.data.history.length}</span>
                </h3>
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Action</th>
                        <th>Arrow</th>
                        <th>Result</th>
                        <th>Blocker</th>
                        <th>Reward</th>
                        <th>Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.data.history.map((action) => (
                        <tr key={action.action_id}>
                          <td>{action.revision}</td>
                          <td>{action.type}</td>
                          <td>{action.arrow_id}</td>
                          <td>
                            <span className={`admin-status ${action.result}`}>{action.result}</span>
                          </td>
                          <td>{action.blocked_by ?? '—'}</td>
                          <td>{action.reward}</td>
                          <td className="admin-date">{date(action.at)}</td>
                        </tr>
                      ))}
                      {!detail.data.history.length && (
                        <tr>
                          <td colSpan={7} className="admin-empty">
                            No actions recorded yet.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </>
            )}
            <div className="admin-detail-footer">
              <button
                className="admin-danger"
                onClick={() => {
                  setTarget({ type: detail.type, id: detail.data.id })
                  setDetail(null)
                }}
              >
                <Trash2 size={16} /> Delete {detail.type}
              </button>
              <button className="admin-secondary" onClick={() => setDetail(null)}>
                Close
              </button>
            </div>
          </section>
        </div>
      )}
      {target && (
        <div className="admin-overlay">
          <section
            className="admin-confirm"
            role="alertdialog"
            aria-modal="true"
            aria-labelledby="delete-title"
          >
            <div className="admin-delete-icon">
              <Trash2 size={24} />
            </div>
            <h2 id="delete-title">Delete this {target.type}?</h2>
            <p>
              {target.type === 'player'
                ? 'This removes the player, their progress, and all associated sessions.'
                : 'This removes the saved attempt and its action history. Progress will be recalculated from remaining wins.'}{' '}
              This cannot be undone.
            </p>
            <code>{target.id}</code>
            {error && (
              <div className="admin-error" role="alert">
                {error}
              </div>
            )}
            <div>
              <button className="admin-secondary" onClick={() => setTarget(null)} disabled={busy}>
                Cancel
              </button>
              <button className="admin-danger" onClick={() => void remove()} disabled={busy}>
                {busy ? <LoaderCircle className="admin-spin" size={16} /> : <Trash2 size={16} />}{' '}
                Delete {target.type}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
