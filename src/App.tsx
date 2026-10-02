import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  Droplet,
  Flower2,
  Grid2X2,
  Leaf,
  Lightbulb,
  Maximize2,
  Minus,
  MoveUpRight,
  Plus,
  RotateCcw,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Star,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { api, ApiError } from './api'
import Board from './Board'
import { ActionQueue } from './ActionQueue'
import type { GameState, Level, LevelProgress, LevelSummary, Player, Preferences } from './types'

const PALETTES = [
  { id: 'olive', name: 'Olive', color: '#6b7954' },
  { id: 'coral', name: 'Coral', color: '#d47e72' },
  { id: 'ocean', name: 'Ocean', color: '#3b9ca8' },
  { id: 'ink', name: 'Ink', color: '#6485b5' },
]
const PREF_KEY = 'amaze-go-preferences-v1'
const DEFAULT_PREFS: Preferences = {
  palette: 'olive',
  sound: false,
  motion: !matchMedia('(prefers-reduced-motion: reduce)').matches,
  dots: true,
}
function loadPreferences(): Preferences {
  try {
    return { ...DEFAULT_PREFS, ...JSON.parse(localStorage.getItem(PREF_KEY) || '{}') }
  } catch {
    return DEFAULT_PREFS
  }
}
const pad = (n: number) => String(n).padStart(2, '0')
const textError = (error: unknown) => (error instanceof Error ? error.message : 'Please try again.')

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <a className="brand" href="/" aria-label="Amaze Go home">
      <span className="brand-symbol">
        <svg viewBox="0 0 32 32" fill="none" aria-hidden="true">
          <path
            d="M6 24V8h9v16h11V8m-5 5 5-5 5 5"
            stroke="currentColor"
            strokeWidth="2.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      {!compact && (
        <span>
          amaze<span className="brand-go">go.</span>
        </span>
      )}
    </a>
  )
}

function Modal({
  title,
  label,
  children,
  onClose,
  wide = false,
}: {
  title: string
  label: string
  children: React.ReactNode
  onClose: () => void
  wide?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    document.body.style.overflow = 'hidden'
    ref.current?.focus()
    const handler = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close.current()
      if (event.key !== 'Tab' || !ref.current) return
      const elements = [
        ...ref.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input, a[href], [tabindex="0"]',
        ),
      ]
      const first = elements[0],
        last = elements.at(-1)
      if (
        event.shiftKey &&
        (document.activeElement === first || document.activeElement === ref.current)
      ) {
        event.preventDefault()
        last?.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first?.focus()
      }
    }
    document.addEventListener('keydown', handler)
    return () => {
      document.body.style.overflow = ''
      document.removeEventListener('keydown', handler)
      previous?.focus()
    }
  }, [])
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div
        className={`modal ${wide ? 'modal-wide' : ''}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        ref={ref}
      >
        <button className="icon-button modal-close" onClick={onClose} aria-label="Close dialog">
          <X size={20} />
        </button>
        <span className="eyebrow">{label}</span>
        <h2>{title}</h2>
        {children}
      </div>
    </div>
  )
}

function LevelPreview({ level, color }: { level: Level | null; color: string }) {
  if (!level) return <div className="preview-skeleton" />
  const rows = level.matrix.length,
    cols = level.matrix[0].length
  return (
    <svg viewBox={`-1 -1 ${cols + 1} ${rows + 1}`} className="level-preview" aria-hidden="true">
      {level.arrows.map((arrow) => {
        const [r, c] = arrow.path.at(-1)!,
          [pr, pc] = arrow.path.at(-2)!
        const dx = c - pc,
          dy = r - pr
        return (
          <g
            key={arrow.id}
            fill={color}
            stroke={color}
            strokeWidth="0.12"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <polyline points={arrow.path.map(([r, c]) => `${c},${r}`).join(' ')} fill="none" />
            <polygon
              points={`${c + dx * 0.1},${r + dy * 0.1} ${c - dx * 0.24 - dy * 0.18},${r - dy * 0.24 + dx * 0.18} ${c - dx * 0.24 + dy * 0.18},${r - dy * 0.24 - dx * 0.18}`}
              stroke="none"
            />
          </g>
        )
      })}
    </svg>
  )
}

function LevelPicker({
  levels,
  current,
  progress,
  color,
  onChoose,
  onClose,
}: {
  levels: LevelSummary[]
  current: string
  progress: Record<string, LevelProgress>
  color: string
  onChoose: (id: string) => void
  onClose: () => void
}) {
  const [filter, setFilter] = useState('all')
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(current)
  const [preview, setPreview] = useState<Level | null>(null)
  const [previewError, setPreviewError] = useState(false)
  useEffect(() => {
    let cancelled = false
    setPreview(null)
    setPreviewError(false)
    api
      .level(selected)
      .then((level) => {
        if (!cancelled) setPreview(level)
      })
      .catch(() => {
        if (!cancelled) setPreviewError(true)
      })
    return () => {
      cancelled = true
    }
  }, [selected])
  const visible = levels.filter(
    (l) =>
      (filter === 'all' || l.difficulty === filter) &&
      `${l.name} ${l.number}`.toLowerCase().includes(query.toLowerCase()),
  )
  const chosen = levels.find((l) => l.id === selected)!
  return (
    <Modal title="A new tangle awaits." label="EXPLORE THE LEVELS" onClose={onClose} wide>
      <p className="modal-intro">Start small. Think big. Every puzzle has a way out.</p>
      <div className="level-picker-layout">
        <div>
          <label className="search-field">
            <Search size={17} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a level…"
              aria-label="Search levels"
            />
          </label>
          <div className="filter-tabs" role="group" aria-label="Difficulty filter">
            {['all', 'easy', 'medium', 'hard', 'expert'].map((f) => (
              <button key={f} onClick={() => setFilter(f)} className={f === filter ? 'active' : ''}>
                {f}
              </button>
            ))}
          </div>
          <div className="all-levels-grid">
            {visible.map((l) => (
              <button
                key={l.id}
                className={`level-cell ${selected === l.id ? 'selected' : ''} ${progress[l.id] ? 'completed' : ''}`}
                onClick={() => setSelected(l.id)}
                aria-label={`Preview level ${l.number}: ${l.name}${progress[l.id] ? ', completed' : ''}`}
              >
                <span>{pad(l.number)}</span>
                {progress[l.id] ? (
                  <Check size={13} />
                ) : (
                  <span className={`difficulty-dot ${l.difficulty}`} />
                )}
              </button>
            ))}
            {!visible.length && (
              <p className="empty-search">No levels found. Try another search.</p>
            )}
          </div>
          <span className="picker-total">{visible.length} puzzles · all available to play</span>
        </div>
        <div className="preview-panel">
          <span className="eyebrow">LEVEL {pad(chosen.number)}</span>
          {previewError ? (
            <p className="preview-error">
              Preview unavailable. You can still try starting this level.
            </p>
          ) : (
            <LevelPreview level={preview} color={color} />
          )}
          <h3>{chosen.name}</h3>
          <span className={`difficulty-badge ${chosen.difficulty}`}>{chosen.difficulty}</span>
          <div className="preview-details">
            <span>
              {chosen.rows} × {chosen.columns} grid
            </span>
            <span>{chosen.arrow_count} arrows</span>
          </div>
          <button className="primary-button" onClick={() => onChoose(selected)}>
            Play this level <ArrowRight size={17} />
          </button>
        </div>
      </div>
    </Modal>
  )
}

function HelpContent() {
  return (
    <>
      <p className="modal-intro">A clear head. A clear path. A beautifully empty board.</p>
      <div className="help-demo">
        <svg viewBox="0 0 280 100" aria-hidden="true">
          <path
            d="M32 74V28h65v46h52V28h74"
            fill="none"
            stroke="#6b7954"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          <path d="m212 20 12 8-12 8" fill="#6b7954" />
          <path d="M233 28h35" stroke="#b6bea4" strokeWidth="2" strokeDasharray="4 5" />
          <path d="M173 73h70" stroke="#d7aa66" strokeWidth="3" strokeLinecap="round" />
          <path d="m233 65 12 8-12 8" fill="#d7aa66" />
        </svg>
        <span>A little space makes all the difference.</span>
      </div>
      <div className="help-steps">
        <div>
          <span>01</span>
          <p>
            <strong>Follow the head.</strong> Each arrow has a direction. Trace the straight line in
            front of its tip.
          </p>
        </div>
        <div>
          <span>02</span>
          <p>
            <strong>Find a clear path.</strong> Tap an arrow when no other arrow blocks its way. It
            follows its path and slides off the board.
          </p>
        </div>
        <div>
          <span>03</span>
          <p>
            <strong>Untangle the rest.</strong> Each arrow you clear makes space for another. Clear
            them all to finish.
          </p>
        </div>
      </div>
      <p className="help-note">
        <Droplet size={18} /> You have three lives. A blocked tap uses one. Hints highlight a safe
        arrow without using a life.
      </p>
      <div className="shortcut-row">
        <span>
          <kbd>H</kbd> Hint
        </span>
        <span>
          <kbd>R</kbd> Restart
        </span>
        <span>
          <kbd>?</kbd> Help
        </span>
        <span>
          <kbd>+</kbd>
          <kbd>−</kbd> Zoom
        </span>
      </div>
    </>
  )
}

export default function App() {
  const [levels, setLevels] = useState<LevelSummary[]>([])
  const [player, setPlayer] = useState<Player | null>(null)
  const [queue] = useState(() => new ActionQueue(api, localStorage))
  const {
    state,
    pendingCount,
    syncing,
    error: syncError,
  } = useSyncExternalStore(queue.subscribe, queue.getSnapshot)
  const [prefs, setPrefs] = useState(loadPreferences)
  const [modal, setModal] = useState<'levels' | 'help' | 'settings' | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const [hintId, setHintId] = useState<number | null>(null)
  const [blockedId, setBlockedId] = useState<number | null>(null)
  const [zoom, setZoom] = useState(1)
  const [elapsed, setElapsed] = useState(0)
  const [syncStatus, setSyncStatus] = useState('Connecting')
  const lock = useRef(false)
  const audio = useRef<AudioContext | null>(null)
  const savedWin = useRef<string | null>(null)
  const changingLevelBlocked = busy || pendingCount > 0 || syncing
  const palette = PALETTES.find((p) => p.id === prefs.palette) || PALETTES[0]
  const progress = player?.progress || {}
  const completed = Object.keys(progress).length
  const perfect = Object.values(progress).filter((p) => p.stars === 3).length

  useEffect(() => {
    localStorage.setItem(PREF_KEY, JSON.stringify(prefs))
  }, [prefs])
  useEffect(() => {
    if (!toast) return
    const timer = window.setTimeout(() => setToast(null), 4200)
    return () => window.clearTimeout(timer)
  }, [toast])
  useEffect(() => {
    if (!hintId) return
    const timer = window.setTimeout(() => setHintId(null), 5000)
    return () => window.clearTimeout(timer)
  }, [hintId, state?.revision])
  useEffect(() => {
    if (!state) return
    const tick = () =>
      setElapsed(
        Math.max(
          0,
          Math.floor(
            (new Date(state.completed_at || Date.now()).getTime() -
              new Date(state.created_at).getTime()) /
              1000,
          ),
        ),
      )
    tick()
    if (state.status !== 'active') return
    const timer = window.setInterval(tick, 1000)
    return () => window.clearInterval(timer)
  }, [state?.session_id, state?.status, state?.completed_at])

  useEffect(() => {
    if (
      !state ||
      state.status !== 'won' ||
      pendingCount ||
      syncError ||
      savedWin.current === state.session_id
    )
      return
    savedWin.current = state.session_id
    void api
      .progress()
      .then(setPlayer)
      .catch((err) => setError(textError(err)))
  }, [state?.session_id, state?.status, pendingCount, syncError])

  useEffect(() => {
    window.addEventListener('online', queue.retry)
    return () => window.removeEventListener('online', queue.retry)
  }, [queue])

  const initialize = useCallback(async () => {
    if (lock.current) return
    lock.current = true
    setLoading(true)
    setError(null)
    try {
      const [catalog, person, health] = await Promise.all([
        api.levels(),
        api.player(),
        api.health(),
      ])
      setLevels(catalog.levels)
      setPlayer(person)
      setSyncStatus(
        health.storage === 'firestore'
          ? health.emulator
            ? 'Saved locally'
            : 'Cloud save on'
          : 'Practice mode',
      )
      let initial: GameState | null = null
      if (person.active_session_id) {
        try {
          initial = await api.session(person.active_session_id)
        } catch (err) {
          if (!(err instanceof ApiError) || err.status !== 404) throw err
        }
      }
      if (!initial) initial = await api.start(catalog.levels[0].id)
      queue.setState(initial)
    } catch (err) {
      setError(textError(err))
      setSyncStatus('Connection paused')
    } finally {
      lock.current = false
      setLoading(false)
    }
  }, [queue])
  useEffect(() => {
    void initialize()
  }, [initialize])

  const playSound = (blocked = false) => {
    if (!prefs.sound) return
    try {
      const ctx = audio.current || new AudioContext()
      audio.current = ctx
      void ctx.resume()
      const oscillator = ctx.createOscillator(),
        gain = ctx.createGain()
      oscillator.type = 'sine'
      oscillator.frequency.setValueAtTime(blocked ? 170 : 660, ctx.currentTime)
      oscillator.frequency.exponentialRampToValueAtTime(blocked ? 110 : 880, ctx.currentTime + 0.15)
      gain.gain.setValueAtTime(0.05, ctx.currentTime)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25)
      oscillator.connect(gain)
      gain.connect(ctx.destination)
      oscillator.start()
      oscillator.stop(ctx.currentTime + 0.25)
    } catch {
      /* Sound is optional when the browser has no audio support. */
    }
  }

  const startLevel = async (id: string) => {
    if (lock.current) return
    if (changingLevelBlocked) {
      setToast('Your moves are still saving. Try again in a moment.')
      return
    }
    lock.current = true
    setBusy(true)
    setError(null)
    try {
      const next = await api.start(id)
      queue.setState(next)
      setHintId(null)
      setBlockedId(null)
      setZoom(1)
      setModal(null)
    } catch (err) {
      setError(textError(err))
    } finally {
      lock.current = false
      setBusy(false)
    }
  }

  const action = (type: 'tap' | 'hint', arrowId?: number) => {
    if (!state || state.status !== 'active' || lock.current) return
    if (type === 'hint' && !state.hints_remaining) {
      setToast('You’ve used your three hints. Take a breath and look for an open path.')
      return
    }
    setError(null)
    try {
      const outcome = queue.enqueue(type, arrowId)
      if (!outcome) return
      if (outcome.result === 'blocked') {
        setBlockedId(arrowId!)
        playSound(true)
        setToast('That path is blocked. Clear the arrow in front first.')
        window.setTimeout(() => setBlockedId(null), 650)
      } else if (outcome.result === 'hint') {
        setHintId(outcome.arrow_id)
        setToast('Follow the golden glow. This arrow has a clear path.')
      } else {
        if (hintId === arrowId) setHintId(null)
        playSound()
      }
    } catch (err) {
      setError(textError(err))
    }
  }

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (
        modal ||
        (e.target as HTMLElement).matches('input, textarea') ||
        e.ctrlKey ||
        e.metaKey ||
        e.altKey ||
        e.repeat
      )
        return
      if (e.key.toLowerCase() === 'h') {
        e.preventDefault()
        void action('hint')
      }
      if (e.key.toLowerCase() === 'r' && state) {
        e.preventDefault()
        void startLevel(state.level_id)
      }
      if (e.key === '?') {
        e.preventDefault()
        setModal('help')
      }
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(3, z + 0.25))
      if (e.key === '-') setZoom((z) => Math.max(1, z - 0.25))
      if (e.key === '0') setZoom(1)
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  })

  const currentIndex = levels.findIndex((l) => l.id === state?.level_id)
  const chapter = Math.max(0, Math.floor(currentIndex / 12))
  const chapterLevels = levels.slice(chapter * 12, chapter * 12 + 12)
  const percentage = state
    ? ((state.level.arrow_count - state.remaining_count) / state.level.arrow_count) * 100
    : 0
  const nextLevel = levels[currentIndex + 1]
  const time = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`
  const chapterNames = [
    'Gentle beginnings',
    'Finding your rhythm',
    'A deeper tangle',
    'The bigger picture',
  ]

  return (
    <div className={`app ${!prefs.motion ? 'reduce-motion' : ''}`}>
      <header className="site-header">
        <div className="header-inner">
          <Brand />
          <nav className="top-nav" aria-label="Main navigation">
            <button className={modal === null ? 'active' : ''} onClick={() => setModal(null)}>
              Play
            </button>
            <button
              className={modal === 'levels' ? 'active' : ''}
              onClick={() => levels.length && setModal('levels')}
            >
              Levels
            </button>
            <button className={modal === 'help' ? 'active' : ''} onClick={() => setModal('help')}>
              How to play <ArrowUpRight size={13} />
            </button>
          </nav>
          <div className="header-actions">
            <span>
              <Leaf size={15} /> A little less noise.
            </span>
            <button
              className="icon-button"
              onClick={() => setModal('settings')}
              aria-label="Game settings"
            >
              <Settings2 size={19} />
            </button>
          </div>
        </div>
      </header>

      <main className="main-shell">
        <section className="hero">
          <div>
            <span className="eyebrow">
              <span className="little-dot" /> A LITTLE ROOM FOR YOUR MIND
            </span>
            <h1>
              Find your flow<span>.</span>
            </h1>
            <p>Untangle the arrows. Enjoy the little victories.</p>
          </div>
          <div className="hero-stats">
            <div>
              <strong>{pad(completed)}</strong>
              <span>levels cleared</span>
            </div>
            <span className="stat-divider" />
            <div>
              <strong>
                {pad(perfect)}
                <Sparkles size={17} />
              </strong>
              <span>perfect puzzles</span>
            </div>
          </div>
        </section>

        {(error || syncError) && (
          <div className="error-banner" role="alert">
            <span>{syncError || error}</span>
            <button
              onClick={() =>
                syncError ? queue.retry() : state ? setError(null) : void initialize()
              }
            >
              {syncError
                ? pendingCount
                  ? 'Retry saving'
                  : 'Continue'
                : state
                  ? 'Dismiss'
                  : 'Try again'}
              <RotateCcw size={14} />
            </button>
          </div>
        )}

        <div className="game-layout">
          <aside className="journey-sidebar">
            <div className="journey-card">
              <div className="section-title">
                <span className="eyebrow">YOUR JOURNEY</span>
                <Grid2X2 size={16} />
              </div>
              <div className="chapter-label">
                CHAPTER {pad(chapter + 1)}{' '}
                <span>
                  {pad(chapter * 12 + 1)} — {pad(Math.min((chapter + 1) * 12, levels.length || 12))}
                </span>
              </div>
              <h2>{chapterNames[chapter] || 'Keep exploring'}</h2>
              <p>One clear path at a time.</p>
              <div className="chapter-levels">
                {(chapterLevels.length
                  ? chapterLevels
                  : Array.from({ length: 12 }, (_, i) => ({
                      id: `loading-${i}`,
                      number: i + 1,
                      name: '',
                    }))
                ).map((l) => (
                  <button
                    className={`chapter-level ${state?.level_id === l.id ? 'current' : ''} ${progress[l.id] ? 'completed' : ''}`}
                    key={l.id}
                    disabled={changingLevelBlocked || loading}
                    onClick={() => void startLevel(l.id)}
                    title={l.name}
                    aria-label={`Play level ${l.number}${progress[l.id] ? ', completed' : ''}`}
                    aria-current={state?.level_id === l.id ? 'step' : undefined}
                  >
                    <span>{pad(l.number)}</span>
                    {progress[l.id] && <Check size={11} />}
                  </button>
                ))}
              </div>
              <div className="chapter-legend">
                <span>
                  <i className="legend-done" /> Completed
                </span>
                <span>
                  <i className="legend-current" /> Playing
                </span>
              </div>
              <button
                className="browse-button"
                disabled={!levels.length}
                onClick={() => setModal('levels')}
              >
                Explore all levels <ArrowUpRight size={16} />
              </button>
            </div>
            <div className="mindful-card">
              <div className="plant-illustration" aria-hidden="true">
                <svg viewBox="0 0 160 100">
                  <path
                    d="M83 90V37m0 30L55 47m28 6 24-26"
                    fill="none"
                    stroke="#7b8b69"
                    strokeWidth="1.5"
                  />
                  <path
                    d="M55 47C33 49 31 26 35 19c20 1 30 9 20 28ZM107 27c-6-19 14-23 25-21-1 15-6 29-25 21ZM82 43C65 39 65 21 72 10c16 5 25 20 10 33Z"
                    fill="#c0c8a8"
                  />
                  <path d="M57 67h51l-6 25H63Z" fill="#dbd4bf" />
                  <path d="M52 67h61" stroke="#b8ac8a" strokeWidth="2" />
                  <path d="M21 93h118" stroke="#dfdcd0" />
                </svg>
              </div>
              <h3>No rush. Just rhythm.</h3>
              <p>
                A small pause can make
                <br />a little room for clarity.
              </p>
              <span>
                <Leaf size={13} /> Play at your own pace
              </span>
            </div>
          </aside>

          <section className="game-card" aria-label="Puzzle game">
            <div className="game-card-header">
              <div className="game-heading">
                <span className="eyebrow">LEVEL {pad(state?.level.number || 1)}</span>
                <h2>{state?.level.name || 'A little untangling'}</h2>
              </div>
              <div className="lives" aria-label={`${state?.lives_remaining ?? 3} lives remaining`}>
                {Array.from({ length: state?.max_lives || 3 }, (_, i) => (
                  <Droplet
                    key={i}
                    size={24}
                    className={i < (state?.lives_remaining ?? 3) ? 'life-full' : 'life-empty'}
                    fill="currentColor"
                    strokeWidth={1.5}
                  />
                ))}
              </div>
            </div>
            <div className="board-meta">
              <span className={`difficulty-badge ${state?.level.difficulty || 'easy'}`}>
                {state?.level.difficulty || 'easy'} <span className="difficulty-dot" />
              </span>
              <span>
                {state ? `${state.level.rows} × ${state.level.columns}` : '11 × 13'} grid
                <span className="meta-dot">·</span>
                {state?.level.arrow_count || '—'} arrows
              </span>
              <span className="timer" aria-label={`Time ${time}`}>
                {time}
              </span>
            </div>
            <div className="board-container">
              {state ? (
                <Board
                  state={state}
                  color={palette.color}
                  zoom={zoom}
                  dots={prefs.dots}
                  motion={prefs.motion}
                  hintId={hintId}
                  blockedId={blockedId}
                  busy={busy || !!syncError}
                  onTap={(id) => void action('tap', id)}
                />
              ) : (
                <div className="board-loading">
                  <Flower2 size={36} className={loading ? 'loading-flower' : ''} />
                  <p>{loading ? 'Finding a little flow…' : 'Your next puzzle is waiting.'}</p>
                  {!loading && (
                    <button className="primary-button" onClick={() => void initialize()}>
                      Reconnect <RotateCcw size={16} />
                    </button>
                  )}
                </div>
              )}
              {state && state.status !== 'active' && (
                <div className="result-overlay">
                  <div className="result-card">
                    <span className="result-icon">
                      {state.status === 'won' ? <Sparkles size={29} /> : <Leaf size={29} />}
                    </span>
                    <span className="eyebrow">
                      {state.status === 'won' ? 'A LITTLE VICTORY' : 'A FRESH PERSPECTIVE'}
                    </span>
                    <h2>
                      {state.status === 'won'
                        ? 'Beautifully untangled.'
                        : 'Let’s take another look.'}
                    </h2>
                    <p>
                      {state.status === 'won'
                        ? 'A clear board. A clearer mind.'
                        : 'Every tangle is a chance to try again.'}
                    </p>
                    {state.status === 'won' && (
                      <div
                        className="result-stars"
                        aria-label={`${Math.max(1, 3 - state.mistakes)} stars`}
                      >
                        {[0, 1, 2].map((i) => (
                          <Star
                            key={i}
                            size={25}
                            fill={i < Math.max(1, 3 - state.mistakes) ? 'currentColor' : 'none'}
                          />
                        ))}
                      </div>
                    )}
                    <div className="result-details">
                      <span>{state.moves} moves</span>
                      <span>{time}</span>
                    </div>
                    <button
                      className="primary-button"
                      disabled={changingLevelBlocked || !!syncError}
                      onClick={() =>
                        void startLevel(
                          state.status === 'won' && nextLevel ? nextLevel.id : state.level_id,
                        )
                      }
                    >
                      {pendingCount
                        ? 'Saving your moves…'
                        : state.status === 'won' && nextLevel
                          ? 'Next little adventure'
                          : 'Try this puzzle again'}
                      <ArrowRight size={17} />
                    </button>
                    {state.status === 'won' && (
                      <button className="text-button" onClick={() => setModal('levels')}>
                        Explore the levels
                      </button>
                    )}
                  </div>
                </div>
              )}
            </div>
            <div className="board-progress">
              <div>
                <span>
                  <span className="remaining-dot" />
                  {state?.remaining_count ?? '—'} arrows remaining
                </span>
                <span>{Math.round(percentage)}% clear</span>
              </div>
              <div className="progress-track">
                <span style={{ width: `${percentage}%` }} />
              </div>
            </div>
            <div className="game-toolbar">
              <div>
                <button
                  className="hint-button"
                  onClick={() => void action('hint')}
                  disabled={
                    busy ||
                    !!syncError ||
                    !state ||
                    state.status !== 'active' ||
                    !state.hints_remaining
                  }
                >
                  <Lightbulb size={18} /> A little hint <span>{state?.hints_remaining ?? 3}</span>
                </button>
                <button
                  className="icon-button restart-button"
                  disabled={changingLevelBlocked || !state}
                  onClick={() => state && void startLevel(state.level_id)}
                  aria-label="Restart level"
                  title="Restart (R)"
                >
                  <RotateCcw size={18} />
                </button>
              </div>
              <div className="zoom-controls">
                <button
                  className="icon-button"
                  disabled={zoom <= 1}
                  onClick={() => setZoom((z) => Math.max(1, z - 0.25))}
                  aria-label="Zoom out"
                >
                  <Minus size={16} />
                </button>
                <span>{Math.round(zoom * 100)}%</span>
                <button
                  className="icon-button"
                  disabled={zoom >= 3}
                  onClick={() => setZoom((z) => Math.min(3, z + 0.25))}
                  aria-label="Zoom in"
                >
                  <Plus size={16} />
                </button>
                <span className="toolbar-divider" />
                <button
                  className="icon-button"
                  onClick={() => setZoom(1)}
                  aria-label="Fit board"
                  title="Fit board (0)"
                >
                  <Maximize2 size={16} />
                </button>
              </div>
            </div>
          </section>

          <aside className="guide-sidebar">
            <div className="guide-card">
              <span className="eyebrow">THE WAY OUT</span>
              <h2>
                Every tangle has
                <br />a way out.
              </h2>
              <p>
                Find an open path.
                <br />
                Let the rest follow.
              </p>
              <div className="guide-mini" aria-hidden="true">
                <svg viewBox="0 0 200 108">
                  <path
                    d="M20 82V24h53v58h49V24h49"
                    stroke="#6b7954"
                    strokeWidth="2.5"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                  <path d="m162 18 10 6-10 6" fill="#6b7954" />
                  <path d="M144 82h28" stroke="#c2a36b" strokeWidth="2.5" strokeLinecap="round" />
                  <path d="m163 76 10 6-10 6" fill="#c2a36b" />
                  <circle
                    cx="176"
                    cy="24"
                    r="13"
                    stroke="#c1c8b3"
                    fill="none"
                    strokeDasharray="2 3"
                  />
                </svg>
              </div>
              <div className="guide-step">
                <span>
                  <MoveUpRight size={17} />
                </span>
                <div>
                  <strong>Follow the arrow</strong>
                  <p>The tip points to its exit.</p>
                </div>
              </div>
              <div className="guide-step">
                <span>
                  <Grid2X2 size={16} />
                </span>
                <div>
                  <strong>Make a little space</strong>
                  <p>Clear paths, one by one.</p>
                </div>
              </div>
              <div className="guide-step">
                <span>
                  <Droplet size={16} />
                </span>
                <div>
                  <strong>Keep your calm</strong>
                  <p>Three lives. Plenty of time.</p>
                </div>
              </div>
              <button className="text-button guide-help" onClick={() => setModal('help')}>
                A quick how-to <ArrowUpRight size={14} />
              </button>
            </div>
            <div className="palette-card">
              <span className="eyebrow">MAKE IT YOURS</span>
              <div className="palette-options" role="group" aria-label="Arrow color">
                {PALETTES.map((p) => (
                  <button
                    key={p.id}
                    style={{ '--swatch': p.color } as React.CSSProperties}
                    className={`palette-swatch ${prefs.palette === p.id ? 'selected' : ''}`}
                    onClick={() => setPrefs((previous) => ({ ...previous, palette: p.id }))}
                    aria-label={`${p.name} arrows`}
                    aria-pressed={prefs.palette === p.id}
                  >
                    {prefs.palette === p.id && <Check size={15} />}
                  </button>
                ))}
                <span>{palette.name}</span>
              </div>
            </div>
            <div className="quick-keys">
              <span>
                <kbd>H</kbd> Hint
              </span>
              <span>
                <kbd>R</kbd> Restart
              </span>
              <button onClick={() => setModal('help')}>
                <kbd>?</kbd> Help
              </button>
            </div>
          </aside>
        </div>

        <div className="under-game">
          <span role="status">
            <ShieldCheck size={14} />{' '}
            {syncError && pendingCount
              ? 'Save paused'
              : pendingCount
                ? `Saving ${pendingCount} move${pendingCount === 1 ? '' : 's'}…`
                : syncStatus}
          </span>
          <p>
            <span>Just one good move.</span> Then another.
          </p>
          <button
            onClick={() => setPrefs((p) => ({ ...p, sound: !p.sound }))}
            aria-label={prefs.sound ? 'Mute sound' : 'Enable sound'}
          >
            {prefs.sound ? <Volume2 size={15} /> : <VolumeX size={15} />} Sound{' '}
            {prefs.sound ? 'on' : 'off'}
          </button>
        </div>
        <footer className="site-footer">
          <span>Made for a moment of clarity.</span>
          <span>
            {levels.length || 1000} puzzles. Countless little victories. <Flower2 size={14} />
          </span>
        </footer>
      </main>

      {toast && (
        <div className="toast" role="status">
          <Leaf size={17} />
          <span>{toast}</span>
          <button onClick={() => setToast(null)} aria-label="Dismiss notification">
            <X size={14} />
          </button>
        </div>
      )}
      {modal === 'help' && (
        <Modal
          title="A little guide to letting go."
          label="HOW TO PLAY"
          onClose={() => setModal(null)}
        >
          <HelpContent />
          <button className="primary-button" onClick={() => setModal(null)}>
            I’ve got this <ArrowRight size={17} />
          </button>
        </Modal>
      )}
      {modal === 'levels' && state && levels.length > 0 && (
        <LevelPicker
          levels={levels}
          current={state.level_id}
          progress={progress}
          color={palette.color}
          onChoose={(id) => void startLevel(id)}
          onClose={() => setModal(null)}
        />
      )}
      {modal === 'settings' && (
        <Modal title="Your kind of calm." label="GAME SETTINGS" onClose={() => setModal(null)}>
          <p className="modal-intro">A few small touches to make this space yours.</p>
          <div className="settings-list">
            {[
              {
                key: 'sound' as const,
                title: 'Gentle sounds',
                detail: 'A little feedback with every move.',
              },
              {
                key: 'motion' as const,
                title: 'Arrow animations',
                detail: 'Watch each tangle slide away.',
              },
              {
                key: 'dots' as const,
                title: 'Subtle grid',
                detail: 'A few dots to help you find your way.',
              },
            ].map((item) => (
              <label className="setting-row" key={item.key}>
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.detail}</small>
                </span>
                <input
                  type="checkbox"
                  role="switch"
                  checked={prefs[item.key]}
                  onChange={(e) => setPrefs((p) => ({ ...p, [item.key]: e.target.checked }))}
                />
                <span className="switch-track" />
              </label>
            ))}
          </div>
          <div className="settings-palette">
            <span className="eyebrow">ARROW COLOR</span>
            <div className="palette-options">
              {PALETTES.map((p) => (
                <button
                  key={p.id}
                  className={`palette-swatch ${prefs.palette === p.id ? 'selected' : ''}`}
                  style={{ '--swatch': p.color } as React.CSSProperties}
                  onClick={() => setPrefs((previous) => ({ ...previous, palette: p.id }))}
                  aria-label={`${p.name} arrows`}
                  aria-pressed={prefs.palette === p.id}
                >
                  {prefs.palette === p.id && <Check size={15} />}
                </button>
              ))}
              <span>{palette.name}</span>
            </div>
          </div>
          <p className="settings-save">
            <ShieldCheck size={15} /> Your progress saves automatically. This browser remembers your
            player.
          </p>
          <button className="primary-button" onClick={() => setModal(null)}>
            Back to the flow <ArrowRight size={17} />
          </button>
        </Modal>
      )}
    </div>
  )
}
