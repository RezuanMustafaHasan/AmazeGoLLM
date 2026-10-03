import { describe, expect, it } from 'vitest'
import {
  initialLevelSelection,
  levelSelectionLabel,
  selectedAgentLevels,
  selectionError,
  selectionPayload,
  type SelectionForm,
} from './agentLevelSelection'
import type { Difficulty, LevelSummary } from './types'

const level = (number: number, difficulty: Difficulty): LevelSummary => ({
  id: `level-${number}`,
  number,
  difficulty,
  name: 'Test problem',
  shape: 'rectangle',
  rows: 2,
  columns: 2,
  arrow_count: 1,
})
const levels = [
  level(90, 'expert'),
  level(8, 'easy'),
  level(1, 'medium'),
  level(40, 'expert'),
  level(2, 'easy'),
  level(7, 'expert'),
  level(5, 'easy'),
]
const category: SelectionForm = { ...initialLevelSelection, mode: 'category', count: 2 }

describe('agent problem selection', () => {
  it('previews the first N matches in level order without mutating the catalog', () => {
    expect(selectedAgentLevels(levels, category).map((item) => item.number)).toEqual([2, 5])
    expect(levels[0].number).toBe(90)
    expect(selectionError(levels, category)).toBeNull()
  })

  it('includes every expert problem across level-number gaps', () => {
    const allExperts = { ...category, difficulty: 'expert' as const, scope: 'all' as const }
    expect(selectedAgentLevels(levels, allExperts).map((item) => item.number)).toEqual([7, 40, 90])
    expect(selectionPayload(allExperts)).toEqual({
      selection_mode: 'category',
      difficulty: 'expert',
      level_limit: null,
      start_level: null,
      end_level: null,
    })
    expect(levelSelectionLabel(selectionPayload(allExperts), 3)).toBe('All Expert problems (3)')
  })

  it('sends only the applicable selection fields when switching between category and range', () => {
    expect(selectionPayload(category)).toEqual({
      selection_mode: 'category',
      difficulty: 'easy',
      level_limit: 2,
      start_level: null,
      end_level: null,
    })
    expect(selectionPayload({ ...category, mode: 'range', start: 1, end: 7 })).toEqual({
      selection_mode: 'range',
      start_level: 1,
      end_level: 7,
      difficulty: null,
      level_limit: null,
    })
    expect(levelSelectionLabel({ start_level: 1, end_level: 10 }, 10)).toBe('Levels 1–10')
    expect(levelSelectionLabel(selectionPayload(category), 2)).toBe('First 2 Easy problems')
  })

  it('rejects unavailable categories, excessive counts, fractional counts, and missing range levels', () => {
    expect(selectionError(levels, { ...category, difficulty: 'hard' })).toContain('No Hard')
    expect(selectionError(levels, { ...category, count: 10 })).toContain('Only 3 Easy')
    expect(selectionError(levels, { ...category, count: 1.5 })).toContain('whole problem count')
    expect(selectionError(levels, { ...category, count: 0 })).toContain('whole problem count')
    expect(selectionError(levels, { ...category, mode: 'range', start: 2, end: 5 })).toContain(
      'contiguous',
    )
    expect(selectionError(levels, { ...category, mode: 'range', start: 5, end: 2 })).toContain(
      'valid start',
    )
    expect(selectionError([], category)).toContain('Loading')
    const manyLevels = Array.from({ length: 1001 }, (_, index) => level(index + 1, 'easy'))
    expect(selectionError(manyLevels, { ...category, scope: 'all' })).toContain('at most 1,000')
  })
})
