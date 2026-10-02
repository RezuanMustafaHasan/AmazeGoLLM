import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, CheckCircle2, LoaderCircle, RefreshCw, Search, XCircle } from 'lucide-react'

type Request = <T>(path: string, options?: RequestInit) => Promise<T>
interface Model {
  id: string
  label: string
}
interface Configuration {
  configured: boolean
  key_configured: boolean
  base_url_configured: boolean
  base_url: string | null
  configuration_error: string | null
  models: Model[]
}
interface Diagnostic {
  code: string
  message: string
  http_status?: number
  transport_error?: string
}
interface Result {
  model: string
  mode: 'text' | 'image'
  status: 'healthy' | 'warning' | 'error'
  checked_at: string
  latency_ms: number | null
  response: string | null
  response_truncated: boolean
  response_model: string | null
  finish_reason: string | null
  usage: { input_tokens?: number; output_tokens?: number; total_tokens?: number }
  expected_response: string
  error: Diagnostic | null
}
const message = (error: unknown) =>
  error instanceof Error ? error.message : 'The check could not be completed.'
const resultKey = (model: string, mode: string) => `${mode}:${model}`

export default function ApiHealth({ request }: { request: Request }) {
  const [config, setConfig] = useState<Configuration | null>(null)
  const [models, setModels] = useState<Model[]>([])
  const [mode, setMode] = useState<'text' | 'image'>('text')
  const [timeout, setTimeoutSeconds] = useState(30)
  const [customModel, setCustomModel] = useState('')
  const [discovered, setDiscovered] = useState<string[] | null>(null)
  const [results, setResults] = useState<Record<string, Result>>({})
  const [checking, setChecking] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)
  const working = useRef(false)

  const refresh = useCallback(async () => {
    const data = await request<Configuration>('/api-health')
    if (!mounted.current) return
    setConfig(data)
    setModels((previous) => [
      ...data.models,
      ...previous.filter((model) => !data.models.some((preset) => preset.id === model.id)),
    ])
  }, [request])

  useEffect(() => {
    mounted.current = true
    void refresh().catch((err) => {
      if (mounted.current) setError(message(err))
    })
    return () => {
      mounted.current = false
    }
  }, [refresh])

  async function check(selected: Model[]) {
    if (working.current || !config?.configured) return
    working.current = true
    setBusy(true)
    setError(null)
    try {
      // One bounded server request per model; show progress as each response arrives.
      for (const model of selected) {
        if (!mounted.current) break
        setChecking(model.id)
        const result = await request<Result>('/api-health/check', {
          method: 'POST',
          body: JSON.stringify({ model: model.id, mode, timeout_seconds: timeout }),
          signal: AbortSignal.timeout((timeout + 15) * 1000),
        })
        if (!mounted.current) break
        setResults((previous) => ({ ...previous, [resultKey(model.id, mode)]: result }))
      }
    } catch (err) {
      if (mounted.current) setError(message(err))
    } finally {
      working.current = false
      if (mounted.current) {
        setChecking(null)
        setBusy(false)
      }
    }
  }

  async function discover() {
    if (working.current) return
    working.current = true
    setBusy(true)
    setError(null)
    try {
      const data = await request<{ status: string; models: string[]; error: Diagnostic | null }>(
        '/api-health/models',
        { method: 'POST', signal: AbortSignal.timeout(35000) },
      )
      if (!mounted.current) return
      if (data.error) {
        setError(`${data.error.message} You can still check a model by entering its ID.`)
      } else {
        setDiscovered(data.models)
      }
    } catch (err) {
      if (mounted.current) setError(message(err))
    } finally {
      working.current = false
      if (mounted.current) setBusy(false)
    }
  }

  const visibleResults = models.map((m) => results[resultKey(m.id, mode)]).filter(Boolean)
  const healthy = visibleResults.filter((result) => result.status === 'healthy').length

  return (
    <div className="health-workspace">
      {error && (
        <div className="admin-error" role="alert">
          {error}
        </div>
      )}
      <section className="admin-card">
        <div className="admin-card-heading">
          <div>
            <h2>UFL gateway</h2>
            <p>One API key and endpoint for all your models.</p>
          </div>
          <Activity size={22} />
        </div>
        <div className="health-connection">
          <div>
            <span>Endpoint</span>
            <code>{config?.base_url ?? 'Not configured'}</code>
          </div>
          <div>
            <span>Server configuration</span>
            <p>
              <i className={config?.key_configured ? 'ready' : ''} /> UFL_API_KEY{' '}
              {config?.key_configured ? 'set' : 'missing'}
            </p>
            <p>
              <i className={config?.base_url_configured ? 'ready' : ''} /> UFL_BASE_URL{' '}
              {config?.base_url_configured ? 'set' : 'missing or invalid'}
            </p>
          </div>
          <button
            className="admin-secondary"
            disabled={busy}
            onClick={() => void refresh().catch((err) => setError(message(err)))}
          >
            <RefreshCw size={16} /> Reload configuration
          </button>
        </div>
        {config?.configuration_error && (
          <p className="health-config-error" role="alert">
            {config.configuration_error}
          </p>
        )}
      </section>

      <section className="admin-card">
        <div className="admin-card-heading">
          <div>
            <h2>Model checks</h2>
            <p>Text checks verify an expected reply. Image checks verify a blue PNG.</p>
          </div>
          <span className="admin-muted">
            {healthy} / {models.length} passed
          </span>
        </div>
        <div className="health-controls">
          <label>
            Check type
            <select
              value={mode}
              disabled={busy}
              onChange={(e) => setMode(e.target.value as 'text' | 'image')}
            >
              <option value="text">Text response</option>
              <option value="image">Image response</option>
            </select>
          </label>
          <label>
            Timeout per model
            <select
              value={timeout}
              disabled={busy}
              onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
            >
              {[15, 30, 60, 120].map((seconds) => (
                <option key={seconds} value={seconds}>
                  {seconds} seconds
                </option>
              ))}
            </select>
          </label>
          <button
            className="admin-secondary"
            disabled={busy || !config?.configured}
            onClick={() => void discover()}
          >
            <Search size={16} /> Discover model IDs
          </button>
          <button
            className="admin-primary"
            disabled={busy || !config?.configured || !models.length}
            onClick={() => void check(models)}
          >
            {checking ? <LoaderCircle size={16} className="admin-spin" /> : <Activity size={16} />}
            {checking ? 'Checking models…' : 'Check all models'}
          </button>
        </div>
        <p className="health-note">
          Checks make real API calls and may use credits. They run only when you click Check.
          Leaving this page stops any remaining checks.
        </p>
        {discovered && (
          <div className="health-discovered">
            <label>
              Available gateway IDs ({discovered.length})
              <select value="" disabled={busy} onChange={(e) => setCustomModel(e.target.value)}>
                <option value="">Choose an ID to add</option>
                {discovered.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>
            <p className="admin-muted">
              Model listing does not verify that a completion succeeds. Run a check to confirm.
            </p>
          </div>
        )}
        <form
          className="health-custom"
          onSubmit={(event) => {
            event.preventDefault()
            const id = customModel.trim()
            if (id && !models.some((model) => model.id === id))
              setModels((previous) => [...previous, { id, label: id }])
            setCustomModel('')
          }}
        >
          <label htmlFor="health-model-id">Custom gateway model ID</label>
          <input
            id="health-model-id"
            value={customModel}
            onChange={(e) => setCustomModel(e.target.value)}
            maxLength={120}
            placeholder="Exact model ID from UFL"
            required
            disabled={busy}
          />
          <button className="admin-secondary" disabled={busy || !customModel.trim()}>
            Add model
          </button>
        </form>
        <p className="health-note">
          Presets use the requested names. If UFL uses a different alias, discover or add the exact
          ID. Gemini 3.8 Flash uses the editable preset gemini-3.8-flash.
        </p>

        <div className="health-models" aria-live="polite" aria-busy={busy}>
          {models.map((model) => {
            const result = results[resultKey(model.id, mode)]
            const pending = checking === model.id
            return (
              <article className="health-model" key={model.id}>
                <div className="health-model-heading">
                  <div>
                    <h3>{model.label}</h3>
                    <code>{model.id}</code>
                  </div>
                  <span
                    className={`health-status ${pending ? 'checking' : (result?.status ?? '')}`}
                  >
                    {pending ? (
                      <LoaderCircle size={14} className="admin-spin" />
                    ) : result?.status === 'healthy' ? (
                      <CheckCircle2 size={14} />
                    ) : result?.status === 'error' ? (
                      <XCircle size={14} />
                    ) : null}
                    {pending
                      ? 'Checking'
                      : result?.status === 'healthy'
                        ? 'Passed'
                        : result?.status === 'warning'
                          ? 'Review reply'
                          : result?.status === 'error'
                            ? 'Failed'
                            : 'Unchecked'}
                  </span>
                  <button
                    className="admin-secondary"
                    disabled={busy || !config?.configured}
                    onClick={() => void check([model])}
                    aria-label={`Check ${model.label}`}
                  >
                    Check
                  </button>
                </div>
                {result && (
                  <div className="health-result">
                    <p className="health-result-meta">
                      {result.latency_ms ?? '—'} ms · {result.usage.total_tokens ?? '—'} tokens ·{' '}
                      {new Date(result.checked_at).toLocaleString()}
                      {result.finish_reason && ` · finish: ${result.finish_reason}`}
                      {result.error?.http_status && ` · HTTP ${result.error.http_status}`}
                    </p>
                    {result.response_model && result.response_model !== model.id && (
                      <p className="admin-muted">Returned model: {result.response_model}</p>
                    )}
                    {result.error && (
                      <p className={`health-diagnostic ${result.status}`}>{result.error.message}</p>
                    )}
                    {result.error?.transport_error && (
                      <p className="admin-muted">HTTP transport: {result.error.transport_error}</p>
                    )}
                    {result.response !== null && (
                      <pre>
                        {result.response}
                        {result.response_truncated ? '\n[Response preview truncated]' : ''}
                      </pre>
                    )}
                  </div>
                )}
                {discovered && !discovered.includes(model.id) && (
                  <p className="health-diagnostic warning">
                    This ID is not listed for your key. Choose an exact gateway ID from the
                    discovered list or confirm this alias with UFL.
                  </p>
                )}
              </article>
            )
          })}
        </div>
      </section>
    </div>
  )
}
