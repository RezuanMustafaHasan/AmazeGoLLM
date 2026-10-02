import { describe, expect, it } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import ThinkingControl from './ThinkingControl'
import {
  healthResultKey,
  selectedThinking,
  thinkingProfile,
  type ThinkingCatalog,
} from './modelThinking'

const catalog: ThinkingCatalog = {
  'claude-opus-5-5': {
    family: 'anthropic',
    label: 'Thinking effort',
    efforts: ['low', 'medium', 'high', 'xhigh', 'max'],
    description: 'Adaptive thinking.',
  },
  'gemini-3.8-flash': {
    family: 'gemini',
    label: 'Thinking level',
    efforts: ['low', 'medium', 'high'],
    description: 'Three thinking levels.',
  },
}

describe('model-specific thinking controls', () => {
  it('keeps choices separate when changing models and never submits an unsupported choice', () => {
    const selections = { 'claude-opus-5-5': 'max', 'gemini-3.8-flash': 'low' }
    expect(selectedThinking('claude-opus-5-5', catalog, selections)).toBe('max')
    expect(selectedThinking('gemini-3.8-flash', catalog, selections)).toBe('low')
    expect(selectedThinking('claude-opus-5-5', catalog, selections)).toBe('max')
    expect(selectedThinking('unknown-alias', catalog, selections)).toBeNull()
    expect(selectedThinking('gemini-3.8-flash', catalog, { 'gemini-3.8-flash': 'max' })).toBeNull()
  })

  it('recognizes native names, provider prefixes, and dated snapshots without guessing new models', () => {
    expect(thinkingProfile('anthropic/claude-opus-5-5-20260901', catalog)).toBe(
      catalog['claude-opus-5-5'],
    )
    expect(thinkingProfile(' Claude-Opus-5-5-2026-09-01 ', catalog)).toBe(
      catalog['claude-opus-5-5'],
    )
    expect(thinkingProfile('gemini-3.8-pro', catalog)).toBeUndefined()
    expect(thinkingProfile('constructor', catalog)).toBeUndefined()
    expect(thinkingProfile('__proto__', catalog)).toBeUndefined()
  })

  it('renders the available values for each model and only default for an unknown ID', () => {
    const render = (model: string) =>
      renderToStaticMarkup(
        createElement(ThinkingControl, {
          model,
          catalog,
          value: null,
          onChange: () => {},
        }),
      )
    expect(render('claude-opus-5-5')).toContain('value="max"')
    expect(render('gemini-3.8-flash')).toContain('Thinking level')
    expect(render('gemini-3.8-flash')).not.toContain('value="max"')
    expect(render('unknown-alias')).toContain('disabled=""')
    expect(render('unknown-alias')).toContain('Gateway default')
  })

  it('keeps health results for default and explicit efforts separate', () => {
    const key = healthResultKey('claude-opus-5-5', 'image', 'max')
    expect(key).not.toBe(healthResultKey('claude-opus-5-5', 'image'))
    expect(key).not.toBe(healthResultKey('claude-opus-5-5', 'image', 'low'))
    expect(key).not.toBe(healthResultKey('claude-opus-5-5', 'text', 'max'))
    expect(key).not.toBe(healthResultKey('claude-opus-5-5', 'image', 'max', 8192))
  })
})
