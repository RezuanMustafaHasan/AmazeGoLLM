import { effortLabel, thinkingProfile, type ThinkingCatalog } from './modelThinking'

export default function ThinkingControl({
  model,
  catalog,
  value,
  onChange,
  disabled = false,
}: {
  model: string
  catalog: ThinkingCatalog
  value: string | null
  onChange: (effort: string | null) => void
  disabled?: boolean
}) {
  const profile = thinkingProfile(model, catalog)
  return (
    <label className="thinking-control">
      {profile?.label ?? 'Thinking effort'}
      <select
        value={value ?? ''}
        disabled={disabled || !profile}
        onChange={(event) => onChange(event.target.value || null)}
      >
        <option value="">Gateway default</option>
        {profile?.efforts.map((effort) => (
          <option key={effort} value={effort}>
            {effortLabel(effort)}
          </option>
        ))}
      </select>
      <small>{profile?.description ?? 'Thinking options are unavailable for this model ID.'}</small>
    </label>
  )
}
