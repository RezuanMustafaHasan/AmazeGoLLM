import type { Arrow, GameState, Outcome, QueuedAction } from './types'

// Immediate visual feedback. FastAPI remains the authority for every queued action.
export function blocker(state: GameState, arrow: Arrow): number | null {
  let [r, c] = arrow.path.at(-1)!
  const [previousRow, previousCol] = arrow.path.at(-2)!
  const dr = r - previousRow,
    dc = c - previousCol
  for (
    r += dr, c += dc;
    r >= 0 && r < state.matrix.length && c >= 0 && c < state.matrix[0].length;
    r += dr, c += dc
  ) {
    const id = state.matrix[r][c]
    if (id > 0 && id !== arrow.id) return id
  }
  return null
}

export function predictAction(
  state: GameState,
  action: QueuedAction,
): { state: GameState; outcome: Outcome } {
  if (state.status !== 'active') throw new Error('This attempt has ended.')
  const next = { ...state, revision: state.revision + 1 }
  let outcome: Outcome
  if (action.type === 'hint') {
    if (!state.hints_remaining) throw new Error('No hints remaining.')
    const arrow = state.arrows.find((a) => blocker(state, a) === null)
    if (!arrow) throw new Error('No available moves.')
    next.hints_used += 1
    next.hints_remaining -= 1
    outcome = { result: 'hint', arrow_id: arrow.id, blocked_by: null, reward: 0 }
  } else {
    const arrow = state.arrows.find((a) => a.id === action.arrow_id)
    if (!arrow) throw new Error('That arrow has already left the board.')
    const blockedBy = blocker(state, arrow)
    next.moves += 1
    outcome = {
      result: blockedBy ? 'blocked' : 'cleared',
      arrow_id: arrow.id,
      blocked_by: blockedBy,
      reward: blockedBy ? -1 : 1,
    }
    if (blockedBy) {
      next.mistakes += 1
      next.lives_remaining -= 1
      if (!next.lives_remaining) next.status = 'lost'
    } else {
      next.removed_ids = [...state.removed_ids, arrow.id]
      next.arrows = state.arrows.filter((a) => a.id !== arrow.id)
      next.matrix = state.matrix.map((row) => row.map((id) => (id === arrow.id ? 0 : id)))
      next.remaining_count -= 1
      if (!next.remaining_count) {
        next.status = 'won'
        outcome.reward = 10
      }
    }
    if (next.status !== 'active') next.completed_at = new Date().toISOString()
  }
  next.legal_arrow_ids =
    next.status === 'active'
      ? next.arrows.filter((a) => blocker(next, a) === null).map((a) => a.id)
      : []
  return { state: next, outcome }
}
