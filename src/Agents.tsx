import { useCallback, useEffect, useRef, useState } from 'react'
import {
  ArrowRight,
  Bot,
  ChevronLeft,
  ChevronRight,
  Download,
  Heart,
  LoaderCircle,
  Pause,
  Play,
  Plus,
  RefreshCw,
  Square,
  StepForward,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import type { GameState, LevelSummary } from './types'
import { canResume, nextTurnDelay } from './agentPlayback'

type Request = <T>(path: string, options?: RequestInit) => Promise<T>
interface Provider {
  id: string
  label: string
  model: string
  models: { id: string; label: string }[]
  configured: boolean
  base_url_configured: boolean
  env: string
}
interface Config {
  name: string
  provider: string
  model: string
  start_level: number
  end_level: number
  lives: number
  observation_mode: 'image_only' | 'image_and_state'
  max_turns_per_level: number
  max_output_tokens: number
  timeout_seconds: number
}
interface Result {
  session_id: string
  level_id: string
  status: string
  moves: number
  mistakes: number
  lives_remaining: number
}
interface Run {
  id: string
  config: Config
  level_ids: string[]
  level_index: number
  status: string
  in_flight: boolean
  turn_count: number
  results: Result[]
  error: string | null
  diagnostic?: { code: string; http_status?: number; retryable: boolean }
  retry_at?: number | null
  consecutive_failures?: number
  request_max_output_tokens?: number
  created_at: string
}
interface Detail extends Run {
  state: Omit<GameState, 'legal_arrow_ids'>
  feedback: string
  system_prompt: string
  lease_expires_at: number | null
}
interface Snapshot {
  revision: number
  lives_remaining: number
  moves: number
  mistakes: number
  remaining_count: number
  status: string
}
interface Turn {
  id: string
  number: number
  session_id: string
  level_id: string
  status: string
  system_prompt: string
  prompt: string
  raw_response: string | null
  decision: { arrow_id: number; explanation: string } | null
  outcome: {
    result: string
    arrow_id: number | null
    blocked_by: number | null
    message?: string
  } | null
  before: Snapshot
  after?: Snapshot
  error: string | null
  usage: { input_tokens?: number; output_tokens?: number; total_tokens?: number }
  latency_ms: number | null
  finish_reason?: string
  max_output_tokens?: number
  diagnostic?: { code: string; http_status?: number; retryable: boolean }
  created_at: string
}
interface Page<T> {
  items: T[]
  next_cursor: string | null
}
interface Turns {
  items: Turn[]
  next_before: number | null
}

const initialConfig: Config = {
  name: 'Visual puzzle agent',
  provider: 'ufl',
  model: 'gpt-6-luna',
  start_level: 1,
  end_level: 1,
  lives: 3,
  observation_mode: 'image_only',
  max_turns_per_level: 2000,
  max_output_tokens: 4096,
  timeout_seconds: 90,
}
const errorMessage = (error: unknown) =>
  error instanceof Error ? error.message : 'Request failed.'
const levelNumber = (id: string) => Number(id.replace('level-', '')) || id
const timestamp = (value: string) => new Date(value).toLocaleString()

export default function Agents({ request }: { request: Request }) {
  const [providers, setProviders] = useState<Provider[]>([])
  const [levels, setLevels] = useState<LevelSummary[]>([])
  const [runs, setRuns] = useState<Page<Run>>({ items: [], next_cursor: null })
  const [run, setRun] = useState<Detail | null>(null)
  const [turns, setTurns] = useState<Turns>({ items: [], next_before: null })
  const [selectedTurn, setSelectedTurn] = useState<Turn | null>(null)
  const [config, setConfig] = useState(initialConfig)
  const [apiKey, setApiKey] = useState('')
  const [showSetup, setShowSetup] = useState(true)
  const [busy, setBusy] = useState(false)
  const [controlling, setControlling] = useState(false)
  const [auto, setAuto] = useState(false)
  const [delay, setDelay] = useState(1000)
  const [error, setError] = useState<string | null>(null)
  const [imagePhase, setImagePhase] = useState<'before' | 'after'>('before')
  const [boardZoom, setBoardZoom] = useState(1)
  const [clientRetryAt, setClientRetryAt] = useState(0)
  const clientFailures = useRef(0)
  const working = useRef(false)
  const mounted = useRef(true)
  const selectedId = useRef<string | null>(null)
  const retryId = useRef<string | null>(null)

  const refresh = useCallback(async () => {
    const [providerData, runData, levelData] = await Promise.all([
      request<{ providers: Provider[] }>('/agents/providers'),
      request<Page<Run>>('/agents'),
      fetch('/api/v1/levels').then(async (response) => {
        if (!response.ok) throw new Error('Could not load the level catalog.')
        return response.json() as Promise<{ levels: LevelSummary[] }>
      }),
    ])
    if (!mounted.current) return
    setProviders(providerData.providers)
    setRuns(runData)
    setLevels(levelData.levels)
  }, [request])

  const load = useCallback(
    async (id: string) => {
      const [detail, history] = await Promise.all([
        request<Detail>(`/agents/${id}`),
        request<Turns>(`/agents/${id}/turns`),
      ])
      if (!mounted.current || selectedId.current !== id) return
      setRun(detail)
      setRuns((previous) => ({
        ...previous,
        items: previous.items.map((item) => (item.id === id ? detail : item)),
      }))
      setTurns(history)
      return detail
    },
    [request],
  )

  useEffect(() => {
    mounted.current = true
    void refresh().catch((err) => setError(errorMessage(err)))
    return () => {
      mounted.current = false
    }
  }, [refresh])

  useEffect(() => {
    if (!run) return
    const id = run.id
    const timer = window.setInterval(() => {
      if (!working.current) void load(id).catch((err) => setError(errorMessage(err)))
    }, 5000)
    return () => window.clearInterval(timer)
  }, [run?.id, load])

  async function inspect(id: string) {
    setAuto(false)
    selectedId.current = id
    retryId.current = null
    clientFailures.current = 0
    setClientRetryAt(0)
    setBusy(true)
    setSelectedTurn(null)
    setError(null)
    try {
      await load(id)
      setShowSetup(false)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function create(event: React.FormEvent) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setAuto(false)
    try {
      const detail = await request<Detail>('/agents', {
        method: 'POST',
        body: JSON.stringify({ ...config, ...(apiKey ? { api_key: apiKey } : {}) }),
      })
      setApiKey('')
      selectedId.current = detail.id
      retryId.current = null
      clientFailures.current = 0
      setClientRetryAt(0)
      setRun(detail)
      setTurns({ items: [], next_before: null })
      setSelectedTurn(null)
      setShowSetup(false)
      await refresh()
    } catch (err) {
      setApiKey('')
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const step = useCallback(async () => {
    if (!run || working.current) return
    working.current = true
    setBusy(true)
    setError(null)
    const id = run.id
    const requestId = retryId.current ?? crypto.randomUUID()
    retryId.current = requestId
    try {
      const detail = await request<Detail>(`/agents/${id}/step`, {
        method: 'POST',
        body: JSON.stringify({ request_id: requestId }),
        signal: AbortSignal.timeout((run.config.timeout_seconds + 60) * 1000),
      })
      retryId.current = null
      clientFailures.current = 0
      setClientRetryAt(0)
      if (!mounted.current || selectedId.current !== id) return
      setRun(detail)
      if (detail.status !== 'running') setAuto(false)
      await load(id)
    } catch (err) {
      if (mounted.current && selectedId.current === id) {
        const latest = await load(id).catch(() => undefined)
        clientFailures.current += 1
        const retry = clientFailures.current < 3 && (!latest || latest.status === 'running')
        if (!retry) setAuto(false)
        setClientRetryAt(Date.now() + 5000 * 2 ** (clientFailures.current - 1))
        setError(
          errorMessage(err) + (retry ? ' Retrying the same request safely.' : ' Resume to retry.'),
        )
      }
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }, [run, request, load])

  useEffect(() => {
    if (!auto || busy || !run || run.status !== 'running') return
    const wait = Math.max(
      nextTurnDelay(delay, run.retry_at, Date.now()),
      clientRetryAt - Date.now(),
    )
    const timer = window.setTimeout(() => void step(), wait)
    return () => window.clearTimeout(timer)
  }, [auto, busy, run, step, delay, clientRetryAt])

  async function control(action: 'resume' | 'pause' | 'stop') {
    if (!run) return
    const id = run.id
    setControlling(true)
    if (action !== 'resume') setAuto(false)
    setError(null)
    try {
      const detail = await request<Detail>(`/agents/${run.id}/control`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!mounted.current || selectedId.current !== id) return
      setRun(detail)
      setRuns((previous) => ({
        ...previous,
        items: previous.items.map((item) => (item.id === id ? detail : item)),
      }))
      if (action === 'resume') {
        clientFailures.current = 0
        setClientRetryAt(0)
        setAuto(true)
      }
    } catch (err) {
      setError(errorMessage(err))
      setAuto(false)
    } finally {
      setControlling(false)
    }
  }

  const provider = providers.find((p) => p.id === config.provider)
  const ended = run?.status === 'completed' || run?.status === 'stopped'
  const activeTurn =
    turns.items.find((turn) => turn.id === selectedTurn?.id) ?? selectedTurn ?? turns.items[0]
  const effectivePhase = activeTurn?.after ? imagePhase : 'before'
  const boardUrl = run
    ? `/api/admin/agents/${run.id}/image?revision=${run.state.revision}&session=${run.state.session_id}`
    : ''
  const turnImageUrl =
    run && activeTurn
      ? `/api/admin/agents/${run.id}/image?turn_id=${activeTurn.id}&phase=${effectivePhase}`
      : ''

  return (
    <div className="agent-workspace">
      {error && (
        <div className="admin-error" role="alert">
          {error}
        </div>
      )}
      <div className="agent-toolbar">
        <p className="admin-muted">One visual decision per turn. Lives reset at every level.</p>
        <button
          className="admin-secondary"
          disabled={busy || auto}
          onClick={() => {
            setShowSetup(!showSetup)
            setApiKey('')
          }}
        >
          {showSetup ? <X size={16} /> : <Plus size={16} />}
          {showSetup ? 'Close setup' : 'New agent session'}
        </button>
      </div>
      {showSetup && (
        <form className="admin-card agent-setup" onSubmit={(event) => void create(event)}>
          <div className="admin-card-heading">
            <div>
              <h2>Create an agent session</h2>
              <p>Select the model, levels, and life budget.</p>
            </div>
            <Bot size={22} />
          </div>
          <div className="agent-form-grid">
            <label>
              Agent name
              <input
                required
                maxLength={80}
                value={config.name}
                onChange={(e) => setConfig({ ...config, name: e.target.value })}
              />
            </label>
            <label>
              API connection
              <select
                value={config.provider}
                onChange={(e) => {
                  const next = providers.find((p) => p.id === e.target.value)
                  if (next) {
                    setConfig({ ...config, provider: next.id, model: next.model })
                    setApiKey('')
                  }
                }}
              >
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model preset
              <select
                value={provider?.models.some((m) => m.id === config.model) ? config.model : ''}
                onChange={(e) => {
                  if (e.target.value) setConfig({ ...config, model: e.target.value })
                }}
              >
                <option value="">Custom model ID</option>
                {provider?.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Model ID
              <input
                required
                maxLength={120}
                value={config.model}
                onChange={(e) => setConfig({ ...config, model: e.target.value })}
              />
              <small>Use the exact UFL model ID. Verify image support in API health.</small>
            </label>
            <label>
              UFL API key (optional override)
              <input
                type="password"
                autoComplete="off"
                value={apiKey}
                required={!provider?.configured}
                maxLength={4096}
                placeholder={
                  provider?.configured ? 'Use UFL_API_KEY from the server' : 'Enter a UFL API key'
                }
                onChange={(e) => setApiKey(e.target.value)}
              />
              <small>
                {provider?.configured
                  ? 'A server key is available. An entered key overrides it.'
                  : 'Encrypted on the server; never saved in browser storage.'}
              </small>
            </label>
            <label>
              From level
              <input
                required
                type="number"
                min={levels[0]?.number ?? 1}
                max={levels.at(-1)?.number ?? 1000}
                value={config.start_level}
                onChange={(e) => setConfig({ ...config, start_level: Number(e.target.value) })}
              />
            </label>
            <label>
              Through level
              <input
                required
                type="number"
                min={config.start_level}
                max={levels.at(-1)?.number ?? 1000}
                value={config.end_level}
                onChange={(e) => setConfig({ ...config, end_level: Number(e.target.value) })}
              />
            </label>
            <label>
              Lives per level
              <input
                required
                type="number"
                min={1}
                max={100}
                value={config.lives}
                onChange={(e) => setConfig({ ...config, lives: Number(e.target.value) })}
              />
            </label>
            <label>
              Observation
              <select
                value={config.observation_mode}
                onChange={(e) =>
                  setConfig({
                    ...config,
                    observation_mode: e.target.value as Config['observation_mode'],
                  })
                }
              >
                <option value="image_only">Image + remaining arrow IDs</option>
                <option value="image_and_state">Image + matrix and arrow paths</option>
              </select>
              <small>Both include rules and previous-action feedback.</small>
            </label>
          </div>
          <details className="agent-advanced">
            <summary>Request limits</summary>
            <div className="agent-form-grid">
              <label>
                Turns per level
                <input
                  type="number"
                  required
                  min={1}
                  max={10000}
                  value={config.max_turns_per_level}
                  onChange={(e) =>
                    setConfig({ ...config, max_turns_per_level: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Output token limit
                <input
                  type="number"
                  required
                  min={256}
                  max={16384}
                  value={config.max_output_tokens}
                  onChange={(e) =>
                    setConfig({ ...config, max_output_tokens: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                Timeout (seconds)
                <input
                  type="number"
                  required
                  min={10}
                  max={180}
                  value={config.timeout_seconds}
                  onChange={(e) =>
                    setConfig({ ...config, timeout_seconds: Number(e.target.value) })
                  }
                />
              </label>
            </div>
          </details>
          <div className="agent-setup-footer">
            <p>
              <code>{'{"arrow_id": 12}'}</code> · Only a blocked tap costs one life.
            </p>
            <button
              className="admin-primary"
              type="submit"
              disabled={busy || !providers.length || !provider?.base_url_configured}
            >
              {busy ? <LoaderCircle size={16} className="admin-spin" /> : <Plus size={16} />}
              Create session
            </button>
            {provider && !provider.base_url_configured && (
              <p role="alert">Set a valid UFL_BASE_URL on the server before creating a session.</p>
            )}
          </div>
        </form>
      )}
      <div className="agent-layout">
        <aside className="admin-card agent-session-list">
          <div className="admin-card-heading">
            <div>
              <h2>Agent sessions</h2>
              <p>Independent evaluation runs</p>
            </div>
            <button
              className="admin-link"
              aria-label="Refresh agent sessions"
              onClick={() => void refresh().catch((err) => setError(errorMessage(err)))}
            >
              <RefreshCw size={16} />
            </button>
          </div>
          <div className="agent-run-list">
            {runs.items.map((item) => (
              <button
                key={item.id}
                className={run?.id === item.id ? 'selected' : ''}
                disabled={busy || auto}
                onClick={() => void inspect(item.id)}
              >
                <strong>
                  {item.config.name}
                  <ChevronRight size={14} />
                </strong>
                <span>{item.config.model}</span>
                <small>
                  Levels {item.config.start_level}–{item.config.end_level}
                  <span className={`admin-status ${item.status}`}>
                    {run?.id === item.id ? run.status : item.status}
                  </span>
                </small>
              </button>
            ))}
            {!runs.items.length && <p className="admin-empty">Create a session to begin.</p>}
            {runs.next_cursor && (
              <button
                disabled={busy}
                onClick={async () => {
                  try {
                    const next = await request<Page<Run>>(`/agents?cursor=${runs.next_cursor}`)
                    setRuns({
                      items: [...runs.items, ...next.items],
                      next_cursor: next.next_cursor,
                    })
                  } catch (err) {
                    setError(errorMessage(err))
                  }
                }}
              >
                Load more sessions
              </button>
            )}
          </div>
        </aside>
        {run ? (
          <section className="agent-monitor">
            <div className="admin-card agent-current">
              <div className="admin-card-heading">
                <div>
                  <h2>{run.config.name}</h2>
                  <p>
                    {run.config.provider} · {run.config.model}
                  </p>
                </div>
                <span className={`admin-status ${run.status}`}>
                  {run.in_flight ? 'Request in progress' : run.status}
                </span>
              </div>
              <div className="agent-controls">
                <button
                  className="admin-secondary"
                  onClick={() => void step()}
                  disabled={
                    busy ||
                    auto ||
                    ended ||
                    controlling ||
                    (run.retry_at ?? 0) * 1000 > Date.now() ||
                    (run.in_flight && (run.lease_expires_at ?? 0) * 1000 > Date.now())
                  }
                >
                  {busy ? (
                    <LoaderCircle size={16} className="admin-spin" />
                  ) : (
                    <StepForward size={16} />
                  )}
                  {busy ? 'Waiting for model…' : retryId.current ? 'Retry turn' : 'Single step'}
                </button>
                {(auto || run.status === 'running') && (
                  <button
                    className="admin-secondary"
                    disabled={controlling}
                    onClick={() => void control('pause')}
                  >
                    <Pause size={16} /> Pause
                  </button>
                )}
                {!auto && (
                  <button
                    className="admin-primary"
                    onClick={() => void control('resume')}
                    disabled={busy || controlling || !canResume(run.status, run.in_flight)}
                  >
                    <Play size={16} /> {run.status === 'running' ? 'Continue' : 'Resume session'}
                  </button>
                )}
                <button
                  className="admin-secondary"
                  onClick={() => void control('stop')}
                  disabled={ended || controlling}
                >
                  <Square size={15} /> Stop
                </button>
                {run.status !== 'running' && !run.in_flight && !run.lease_expires_at && (
                  <a
                    className="admin-secondary"
                    href={`/api/admin/agents/${run.id}/history/download`}
                  >
                    <Download size={16} /> Download performance history
                  </a>
                )}
                <label className="agent-speed">
                  Between turns
                  <select value={delay} onChange={(e) => setDelay(Number(e.target.value))}>
                    <option value={0}>No delay</option>
                    <option value={1000}>1 second</option>
                    <option value={3000}>3 seconds</option>
                    <option value={5000}>5 seconds</option>
                  </select>
                </label>
              </div>
              <p className="agent-play-note">
                Pause saves progress after the current request finishes. Resume continues this
                session later, including after reopening the panel. Autoplay runs while this panel
                stays open. Temporary failures retry up to three consecutive failed requests.
              </p>
              {run.error && (
                <div className="admin-error" role="alert">
                  {run.error}
                  {run.diagnostic && (
                    <p className="agent-retry-note">
                      {run.diagnostic.code}
                      {run.diagnostic.http_status && ` · HTTP ${run.diagnostic.http_status}`}
                      {run.status === 'running' &&
                        run.retry_at &&
                        ` · Next retry after ${new Date(run.retry_at * 1000).toLocaleTimeString()}`}
                      {` · Consecutive failures: ${run.consecutive_failures ?? 0}/3`}
                    </p>
                  )}
                </div>
              )}
              <div className="agent-state-bar" aria-live="polite">
                <span>
                  Level <strong>{run.state.level.number}</strong> · {run.level_index + 1}/
                  {run.level_ids.length}
                </span>
                <span>
                  <Heart size={15} />{' '}
                  <strong>
                    {run.state.lives_remaining}/{run.state.max_lives}
                  </strong>{' '}
                  lives
                </span>
                <span>
                  <strong>{run.state.remaining_count}</strong> arrows
                </span>
                <span>
                  <strong>{run.state.moves}</strong> moves · <strong>{run.state.mistakes}</strong>{' '}
                  mistakes
                </span>
                <span className={`admin-status ${run.state.status}`}>{run.state.status}</span>
              </div>
              <div className="agent-zoom-controls">
                <span>Board zoom</span>
                <button
                  className="admin-link"
                  aria-label="Zoom board out"
                  disabled={boardZoom === 1}
                  onClick={() => setBoardZoom(Math.max(1, boardZoom / 2))}
                >
                  <ZoomOut size={16} />
                </button>
                <span>{boardZoom}×</span>
                <button
                  className="admin-link"
                  aria-label="Zoom board in"
                  disabled={boardZoom === 8}
                  onClick={() => setBoardZoom(Math.min(8, boardZoom * 2))}
                >
                  <ZoomIn size={16} />
                </button>
              </div>
              <div className="agent-board">
                <img
                  src={boardUrl}
                  alt={`Current labelled board for level ${run.state.level.number}, revision ${run.state.revision}`}
                  style={
                    boardZoom === 1 ? undefined : { width: `${boardZoom * 100}%`, maxWidth: 'none' }
                  }
                />
              </div>
              <details className="agent-prompt">
                <summary>Next request: rules, state, and feedback</summary>
                <pre>{run.system_prompt}</pre>
                <pre>{run.feedback}</pre>
              </details>
            </div>
            {run.results.length > 0 && (
              <div className="admin-card">
                <div className="admin-card-heading">
                  <div>
                    <h2>Level results</h2>
                    <p>
                      {run.results.filter((r) => r.status === 'won').length} won ·{' '}
                      {run.results.filter((r) => r.status === 'lost').length} lost
                    </p>
                  </div>
                </div>
                <div className="admin-table-wrap">
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>Level</th>
                        <th>Result</th>
                        <th>Moves</th>
                        <th>Mistakes</th>
                        <th>Lives left</th>
                      </tr>
                    </thead>
                    <tbody>
                      {run.results.map((result) => (
                        <tr key={result.session_id}>
                          <td>{levelNumber(result.level_id)}</td>
                          <td>
                            <span className={`admin-status ${result.status}`}>{result.status}</span>
                          </td>
                          <td>{result.moves}</td>
                          <td>{result.mistakes}</td>
                          <td>{result.lives_remaining}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
            <div className="admin-card">
              <div className="admin-card-heading">
                <div>
                  <h2>Action log</h2>
                  <p>
                    {run.turn_count} requests · Click a row to inspect the exact request and result.
                  </p>
                </div>
              </div>
              <div className="admin-table-wrap">
                <table className="admin-table agent-turn-table">
                  <thead>
                    <tr>
                      <th>Turn</th>
                      <th>Level</th>
                      <th>Arrow</th>
                      <th>Outcome</th>
                      <th>Lives</th>
                      <th>Time</th>
                      <th>Tokens</th>
                    </tr>
                  </thead>
                  <tbody>
                    {turns.items.map((turn) => (
                      <tr key={turn.id} className={activeTurn?.id === turn.id ? 'selected' : ''}>
                        <td>
                          <button
                            className="admin-id"
                            onClick={() => {
                              setSelectedTurn(turn)
                              setImagePhase('before')
                            }}
                          >
                            #{turn.number}
                            <ArrowRight size={12} />
                          </button>
                        </td>
                        <td>{levelNumber(turn.level_id)}</td>
                        <td>{turn.outcome?.arrow_id ?? turn.decision?.arrow_id ?? '—'}</td>
                        <td>
                          <span className={`admin-status ${turn.outcome?.result ?? turn.status}`}>
                            {turn.outcome?.result ?? turn.status}
                          </span>
                        </td>
                        <td>
                          {turn.before.lives_remaining}
                          {turn.after && <> → {turn.after.lives_remaining}</>}
                        </td>
                        <td>
                          {turn.latency_ms == null
                            ? '—'
                            : `${(turn.latency_ms / 1000).toFixed(1)}s`}
                        </td>
                        <td>{turn.usage.total_tokens ?? '—'}</td>
                      </tr>
                    ))}
                    {!turns.items.length && (
                      <tr>
                        <td colSpan={7} className="admin-empty">
                          No model requests yet. Use Single step or Play.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
              {turns.next_before && (
                <div className="admin-pagination">
                  <button
                    className="admin-secondary"
                    onClick={async () => {
                      try {
                        const older = await request<Turns>(
                          `/agents/${run.id}/turns?before=${turns.next_before}`,
                        )
                        setTurns({
                          items: [...turns.items, ...older.items],
                          next_before: older.next_before,
                        })
                      } catch (err) {
                        setError(errorMessage(err))
                      }
                    }}
                  >
                    Load older turns
                  </button>
                </div>
              )}
              {activeTurn && (
                <div className="agent-turn-detail">
                  <div className="agent-turn-heading">
                    <h3>Turn #{activeTurn.number}</h3>
                    <small>{timestamp(activeTurn.created_at)}</small>
                    {selectedTurn && (
                      <button className="admin-link" onClick={() => setSelectedTurn(null)}>
                        Follow latest
                      </button>
                    )}
                  </div>
                  {activeTurn.error && <div className="admin-error">{activeTurn.error}</div>}
                  {activeTurn.outcome && (
                    <p className="agent-outcome">
                      {activeTurn.outcome.result === 'blocked'
                        ? `Arrow ${activeTurn.outcome.arrow_id} was blocked by arrow ${activeTurn.outcome.blocked_by}. One life lost.`
                        : activeTurn.outcome.result === 'invalid'
                          ? `Invalid response: ${activeTurn.outcome.message} ${
                              activeTurn.after &&
                              activeTurn.after.lives_remaining < activeTurn.before.lives_remaining
                                ? 'One life was charged by the previous scoring policy.'
                                : 'No life lost.'
                            }`
                          : `Arrow ${activeTurn.outcome.arrow_id} cleared successfully.`}
                    </p>
                  )}
                  {activeTurn.decision?.explanation && (
                    <p className="agent-explanation">
                      <strong>Model explanation</strong> {activeTurn.decision.explanation}
                    </p>
                  )}
                  <div className="agent-image-toggle">
                    <button
                      className={imagePhase === 'before' ? 'selected' : ''}
                      onClick={() => setImagePhase('before')}
                    >
                      <ChevronLeft size={14} /> Image sent to model
                    </button>
                    <button
                      className={imagePhase === 'after' ? 'selected' : ''}
                      disabled={!activeTurn.after}
                      onClick={() => setImagePhase('after')}
                    >
                      After action <ChevronRight size={14} />
                    </button>
                  </div>
                  <div className="agent-turn-image">
                    <img
                      src={turnImageUrl}
                      alt={`Turn ${activeTurn.number}, ${effectivePhase} action`}
                    />
                  </div>
                  <details className="agent-prompt">
                    <summary>Full request text</summary>
                    <pre>{activeTurn.system_prompt}</pre>
                    <pre>{activeTurn.prompt}</pre>
                  </details>
                  <details className="agent-prompt" open>
                    <summary>Raw model response</summary>
                    <pre>{activeTurn.raw_response ?? 'No response received.'}</pre>
                  </details>
                  <p className="admin-footnote">
                    Input tokens: {activeTurn.usage.input_tokens ?? '—'} · Output tokens:{' '}
                    {activeTurn.usage.output_tokens ?? '—'}
                    {' · '}Session {activeTurn.session_id.slice(0, 8)} · Revision{' '}
                    {activeTurn.before.revision}
                    {activeTurn.after && ` → ${activeTurn.after.revision}`}
                    {activeTurn.finish_reason && ` · Finish: ${activeTurn.finish_reason}`}
                    {activeTurn.max_output_tokens &&
                      ` · Output budget: ${activeTurn.max_output_tokens}`}
                  </p>
                </div>
              )}
            </div>
          </section>
        ) : (
          <div className="admin-card agent-placeholder">
            <Bot size={32} />
            <h2>Watch an agent solve the board.</h2>
            <p>Create a session or open a saved run to view its board and decisions.</p>
          </div>
        )}
      </div>
    </div>
  )
}
