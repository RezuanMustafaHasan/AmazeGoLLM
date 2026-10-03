import type { Difficulty, LevelSummary } from './types'

export const problemCategories: Difficulty[] = ['easy', 'medium', 'hard', 'expert']
export const categoryLabel = (category: Difficulty) => category[0].toUpperCase() + category.slice(1)

export interface SelectionConfig {
  selection_mode?: 'range' | 'category'
  start_level?: number | null
  end_level?: number | null
  difficulty?: Difficulty | null
  level_limit?: number | null
}

export interface SelectionForm {
  mode: 'range' | 'category'
  start: number
  end: number
  difficulty: Difficulty
  scope: 'first' | 'all'
  count: number
}

export const initialLevelSelection: SelectionForm = {
  mode: 'range',
  start: 1,
  end: 1,
  difficulty: 'easy',
  scope: 'first',
  count: 10,
}

export function selectionPayload(selection: SelectionForm): SelectionConfig {
  return selection.mode === 'category'
    ? {
        selection_mode: 'category',
        difficulty: selection.difficulty,
        level_limit: selection.scope === 'first' ? selection.count : null,
        start_level: null,
        end_level: null,
      }
    : {
        selection_mode: 'range',
        start_level: selection.start,
        end_level: selection.end,
        difficulty: null,
        level_limit: null,
      }
}

export function selectedAgentLevels(levels: LevelSummary[], selection: SelectionForm) {
  const matching = levels
    .filter((level) =>
      selection.mode === 'category'
        ? level.difficulty === selection.difficulty
        : selection.start <= level.number && level.number <= selection.end,
    )
    .sort((a, b) => a.number - b.number)
  return selection.mode === 'category' && selection.scope === 'first'
    ? matching.slice(0, Math.max(0, Math.trunc(selection.count)))
    : matching
}

export function selectionError(levels: LevelSummary[], selection: SelectionForm) {
  if (!levels.length) return 'Loading available problems…'
  if (selection.mode === 'category') {
    const available = levels.filter((level) => level.difficulty === selection.difficulty).length
    if (!available) return `No ${categoryLabel(selection.difficulty)} problems are available.`
    if (selection.scope === 'first') {
      if (!Number.isInteger(selection.count) || selection.count < 1 || selection.count > 1000)
        return 'Choose a whole problem count between 1 and 1,000.'
      if (selection.count > available)
        return `Only ${available} ${categoryLabel(selection.difficulty)} problems are available. Choose a smaller count or all problems.`
    }
  } else {
    if (
      !Number.isInteger(selection.start) ||
      !Number.isInteger(selection.end) ||
      selection.start < 1 ||
      selection.end < selection.start
    )
      return 'Choose a valid start and end level.'
    if (selectedAgentLevels(levels, selection).length !== selection.end - selection.start + 1)
      return 'Choose a contiguous range of available levels.'
  }
  if (selectedAgentLevels(levels, selection).length > 1000)
    return 'Select at most 1,000 problems per session.'
  return null
}

export function levelSelectionLabel(config: SelectionConfig, levelCount: number) {
  if (config.selection_mode === 'category' && config.difficulty) {
    const category = categoryLabel(config.difficulty)
    return config.level_limit != null
      ? `First ${config.level_limit} ${category} problems`
      : `All ${category} problems (${levelCount})`
  }
  return `Levels ${config.start_level}–${config.end_level}`
}
