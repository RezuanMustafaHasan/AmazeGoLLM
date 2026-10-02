import { predictAction } from './gamePrediction'
import type { BatchResponse, GameState, Outcome, QueuedAction } from './types'

interface Client {
  batch(state: GameState, actions: QueuedAction[]): Promise<BatchResponse>
  session(id: string): Promise<GameState>
}
interface Persistence {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}
interface Snapshot {
  state: GameState | null
  pendingCount: number
  syncing: boolean
  error: string | null
}
const storageKey = (id: string) => `amaze-go-pending-v1:${id}`
const statusOf = (error: unknown): number =>
  typeof error === 'object' && error !== null && 'status' in error ? Number(error.status) : 0

export class ActionQueue {
  private confirmed: GameState | null = null
  private pending: QueuedAction[] = []
  private listeners = new Set<() => void>()
  private running = false
  private snapshot: Snapshot = { state: null, pendingCount: 0, syncing: false, error: null }

  constructor(
    private client: Client,
    private storage?: Persistence,
  ) {}

  getSnapshot = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => {
      this.listeners.delete(listener)
    }
  }

  private publish(state: GameState | null, error: string | null = null) {
    this.snapshot = { state, pendingCount: this.pending.length, syncing: this.running, error }
    if (this.confirmed && this.storage) {
      try {
        const key = storageKey(this.confirmed.session_id)
        if (this.pending.length)
          this.storage.setItem(
            key,
            JSON.stringify({ revision: this.confirmed.revision, actions: this.pending }),
          )
        else this.storage.removeItem(key)
      } catch {
        /* Synchronization still works when browser storage is unavailable. */
      }
    }
    this.listeners.forEach((listener) => listener())
  }

  private replay() {
    let state = this.confirmed!
    for (const action of this.pending) state = predictAction(state, action).state
    return state
  }

  setState(state: GameState) {
    if (this.running || this.pending.length)
      throw new Error('Please let your moves finish saving first.')
    this.confirmed = state
    try {
      const saved = JSON.parse(this.storage?.getItem(storageKey(state.session_id)) || 'null')
      if (saved && Array.isArray(saved.actions)) {
        const processed = new Set(state.processed_action_ids || [])
        const acknowledged = saved.actions.filter((a: QueuedAction) =>
          processed.has(a.action_id),
        ).length
        if (state.revision !== saved.revision + acknowledged)
          throw new Error('The saved board has changed.')
        this.pending = saved.actions.filter((a: QueuedAction) => !processed.has(a.action_id))
      }
      this.publish(this.replay())
    } catch {
      this.pending = []
      this.publish(state, 'The saved board changed. Your latest cloud state has been restored.')
    }
    if (this.pending.length) void this.pump()
  }

  enqueue(type: 'tap' | 'hint', arrowId?: number): Outcome | null {
    if (!this.snapshot.state || this.snapshot.state.status !== 'active' || this.snapshot.error)
      return null
    if (type === 'tap' && !this.snapshot.state.arrows.some((a) => a.id === arrowId)) return null
    const action: QueuedAction = {
      type,
      ...(type === 'tap' ? { arrow_id: arrowId } : {}),
      action_id: crypto.randomUUID(),
    }
    const predicted = predictAction(this.snapshot.state, action)
    this.pending.push(action)
    // Update the board synchronously, before starting any network request.
    this.publish(predicted.state)
    void this.pump()
    return predicted.outcome
  }

  retry = () => {
    if (this.running) return
    this.publish(this.snapshot.state)
    if (this.pending.length) void this.pump()
  }

  private async pump() {
    if (this.running || !this.confirmed || !this.pending.length) return
    this.running = true
    this.publish(this.snapshot.state)
    try {
      while (this.pending.length) {
        const batch = this.pending.slice(0, 100)
        const result = await this.client.batch(this.confirmed, batch)
        this.confirmed = result.state
        this.pending = this.pending.slice(batch.length)
        // Reapply later taps so an older reply never puts an arrow back on screen.
        this.publish(this.replay())
      }
    } catch (error) {
      if (statusOf(error) === 409 || statusOf(error) === 422) {
        try {
          const actual = await this.client.session(this.confirmed.session_id)
          this.confirmed = actual
          this.pending = []
          this.publish(actual, 'Your board changed. The latest saved state has been restored.')
        } catch {
          this.publish(
            this.snapshot.state,
            'Saving is paused. Your pending moves are kept here; retry to reconnect.',
          )
        }
      } else {
        // Keep the exact UUIDs and base revision: a lost response may already have committed.
        this.publish(
          this.snapshot.state,
          'Saving is paused. Your pending moves are kept here; retry to reconnect.',
        )
      }
    } finally {
      this.running = false
      this.publish(this.snapshot.state, this.snapshot.error)
    }
  }
}
