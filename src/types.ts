export type Difficulty = 'easy' | 'medium' | 'hard' | 'expert'
export type Cell = [number, number]
export interface Arrow {
  id: number
  path: Cell[]
}
export interface LevelSummary {
  id: string
  number: number
  name: string
  difficulty: Difficulty
  shape: string
  rows: number
  columns: number
  arrow_count: number
}
export interface Level extends Omit<LevelSummary, 'rows' | 'columns' | 'arrow_count'> {
  schema_version: 1
  lives: number
  matrix: number[][]
  arrows: Arrow[]
}
export interface GameState {
  session_id: string
  level_id: string
  level: LevelSummary
  status: 'active' | 'won' | 'lost'
  lives_remaining: number
  max_lives: number
  moves: number
  mistakes: number
  hints_used: number
  hints_remaining: number
  revision: number
  removed_ids: number[]
  processed_action_ids?: string[]
  remaining_count: number
  matrix: number[][]
  arrows: Arrow[]
  legal_arrow_ids: number[]
  created_at: string
  completed_at: string | null
}
export interface Outcome {
  result: 'cleared' | 'blocked' | 'hint'
  arrow_id: number
  blocked_by: number | null
  reward: number
}
export interface ActionResponse {
  state: GameState
  outcome: Outcome
}
export interface QueuedAction {
  type: 'tap' | 'hint'
  arrow_id?: number
  action_id: string
}
export interface BatchResponse {
  state: GameState
  outcomes: Outcome[]
}
export interface LevelProgress {
  stars: number
  best_moves: number
  best_time_ms: number
  completions: number
}
export interface Player {
  player_id: string
  progress: Record<string, LevelProgress>
  active_session_id: string | null
}
export interface Preferences {
  palette: string
  sound: boolean
  motion: boolean
  dots: boolean
}
