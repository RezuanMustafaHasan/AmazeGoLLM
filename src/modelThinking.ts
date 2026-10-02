export interface ThinkingProfile {
  family: 'openai' | 'anthropic' | 'gemini'
  label: string
  efforts: string[]
  description: string
}
export type ThinkingCatalog = Record<string, ThinkingProfile>
export type ThinkingSelections = Record<string, string | null>

export function thinkingProfile(model: string, catalog: ThinkingCatalog) {
  const name = model
    .trim()
    .toLowerCase()
    .split('/')
    .at(-1)!
    .replace(/-(?:\d{8}|\d{4}-\d{2}-\d{2})$/, '')
  return Object.prototype.hasOwnProperty.call(catalog, name) ? catalog[name] : undefined
}

export function selectedThinking(
  model: string,
  catalog: ThinkingCatalog,
  selections: ThinkingSelections,
) {
  const effort = selections[model.trim()] ?? null
  return effort && thinkingProfile(model, catalog)?.efforts.includes(effort) ? effort : null
}

const effortLabels: Record<string, string> = { none: 'Off', xhigh: 'Extra high' }

export const effortLabel = (effort?: string | null) =>
  effort ? (effortLabels[effort] ?? effort[0].toUpperCase() + effort.slice(1)) : 'Gateway default'

export const healthResultKey = (
  model: string,
  mode: string,
  effort?: string | null,
  outputLimit = 4096,
) => JSON.stringify([model, mode, effort ?? null, outputLimit])
