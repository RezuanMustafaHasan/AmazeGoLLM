import type {
  ActionResponse,
  BatchResponse,
  GameState,
  Level,
  LevelSummary,
  Player,
  QueuedAction,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const KEY = 'amaze-go-player-v1'
let token = localStorage.getItem(KEY)

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message)
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${BASE}/api${path}`, {
      ...options,
      signal: options.signal ?? AbortSignal.timeout(15000),
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...options.headers,
      },
    })
  } catch {
    throw new ApiError('The game server is unavailable. Check your connection and try again.', 0)
  }
  if (!response.ok) {
    const error = await response.json().catch(() => ({}))
    throw new ApiError(
      typeof error.detail === 'string' ? error.detail : 'Something went wrong. Please try again.',
      response.status,
    )
  }
  return response.json() as Promise<T>
}

export const api = {
  health: () => request<{ storage: string; emulator: boolean }>('/health'),
  levels: () => request<{ levels: LevelSummary[] }>('/v1/levels'),
  level: (id: string) => request<Level>(`/v1/levels/${id}`),
  async player(): Promise<Player> {
    if (token) {
      try {
        return await request<Player>('/v1/players/me')
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 401) throw error
      }
    }
    const player = await request<{ access_token: string }>('/v1/players', { method: 'POST' })
    token = player.access_token
    localStorage.setItem(KEY, token)
    return request<Player>('/v1/players/me')
  },
  progress: () => request<Player>('/v1/players/me'),
  start: (levelId: string) =>
    request<GameState>('/v1/sessions', {
      method: 'POST',
      body: JSON.stringify({ level_id: levelId, actor_type: 'human' }),
    }),
  session: (id: string) => request<GameState>(`/v1/sessions/${id}`),
  tap: (state: GameState, arrowId: number, actionId: string) =>
    request<ActionResponse>(`/v1/sessions/${state.session_id}/actions`, {
      method: 'POST',
      body: JSON.stringify({
        arrow_id: arrowId,
        expected_revision: state.revision,
        action_id: actionId,
      }),
    }),
  hint: (state: GameState, actionId: string) =>
    request<ActionResponse>(`/v1/sessions/${state.session_id}/hints`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: state.revision, action_id: actionId }),
    }),
  batch: (state: GameState, actions: QueuedAction[]) =>
    request<BatchResponse>(`/v1/sessions/${state.session_id}/actions/batch`, {
      method: 'POST',
      body: JSON.stringify({ expected_revision: state.revision, actions }),
    }),
}
