import { describe, expect, it, vi } from 'vitest'
import { ActionQueue } from './ActionQueue'
import { predictAction } from './gamePrediction'
import fixtureJSON from './prediction-fixtures.json'
import type { BatchResponse, GameState, Outcome, QueuedAction } from './types'

interface Transition {
  before: GameState
  after: GameState
  action: QueuedAction
  outcome: Outcome
}
const cases = fixtureJSON as unknown as { name: string; transitions: Transition[] }[]
const win = cases.find((c) => c.name === 'win')!.transitions
const blocked = cases.find((c) => c.name === 'blocked')!.transitions[0]

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((yes, no) => {
    resolve = yes
    reject = no
  })
  return { promise, resolve, reject }
}
async function flush() {
  for (let i = 0; i < 6; i++) await Promise.resolve()
}
function memoryStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      values.set(key, value)
    },
    removeItem: (key: string) => {
      values.delete(key)
    },
  }
}
function comparable(state: GameState) {
  const { completed_at: _end, processed_action_ids: _ids, ...fields } = state
  return fields
}

describe('local prediction matches fixtures from the Python engine', () => {
  for (const fixture of cases) {
    it(fixture.name, () => {
      for (const step of fixture.transitions) {
        const before = structuredClone(step.before)
        const predicted = predictAction(before, step.action)
        expect(comparable(predicted.state)).toEqual(comparable(step.after))
        expect(predicted.outcome).toEqual(step.outcome)
        expect(Boolean(predicted.state.completed_at)).toBe(Boolean(step.after.completed_at))
        expect(before).toEqual(step.before)
      }
    })
  }
})

describe('instant play with a delayed network', () => {
  it('removes arrows immediately, accepts the next tap, and keeps it removed after an older reply', async () => {
    const first = deferred<BatchResponse>(),
      second = deferred<BatchResponse>()
    const client = {
      batch: vi
        .fn<(state: GameState, actions: QueuedAction[]) => Promise<BatchResponse>>()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
      session: vi.fn(async () => win[0].before),
    }
    const queue = new ActionQueue(client)
    queue.setState(win[0].before)
    queue.enqueue('tap', 2)
    expect(queue.getSnapshot().state!.remaining_count).toBe(1) // no reply yet
    queue.enqueue('tap', 1)
    expect(queue.getSnapshot().state!.status).toBe('won')
    expect(queue.getSnapshot().state!.remaining_count).toBe(0)
    expect(queue.getSnapshot().pendingCount).toBe(2)
    expect(client.batch).toHaveBeenCalledTimes(1)
    first.resolve({ state: win[0].after, outcomes: [win[0].outcome] })
    await flush()
    expect(queue.getSnapshot().state!.remaining_count).toBe(0)
    expect(client.batch.mock.calls[1][0].revision).toBe(1)
    expect(client.batch.mock.calls[1][1][0].arrow_id).toBe(1)
    second.resolve({ state: win[1].after, outcomes: [win[1].outcome] })
    await flush()
    expect(queue.getSnapshot().pendingCount).toBe(0)
    expect(queue.getSnapshot().state!.moves).toBe(2)
    expect(queue.getSnapshot().syncing).toBe(false)
  })

  it('batches rapid taps without delaying life-loss feedback', async () => {
    const loss = cases.find((c) => c.name === 'loss')!.transitions
    const first = deferred<BatchResponse>(),
      second = deferred<BatchResponse>()
    const client = {
      batch: vi
        .fn<(state: GameState, actions: QueuedAction[]) => Promise<BatchResponse>>()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(second.promise),
      session: vi.fn(async () => loss[0].before),
    }
    const queue = new ActionQueue(client)
    queue.setState(loss[0].before)
    queue.enqueue('tap', 1)
    queue.enqueue('tap', 1)
    queue.enqueue('tap', 1)
    expect(queue.getSnapshot().state!.lives_remaining).toBe(0)
    expect(queue.getSnapshot().pendingCount).toBe(3)
    expect(queue.enqueue('tap', 2)).toBeNull() // terminal prediction refuses extra taps
    first.resolve({ state: loss[0].after, outcomes: [loss[0].outcome] })
    await flush()
    expect(client.batch.mock.calls[1][1]).toHaveLength(2)
    expect(
      new Set(client.batch.mock.calls.flatMap((call) => call[1].map((a) => a.action_id))).size,
    ).toBe(3)
    second.resolve({ state: loss[2].after, outcomes: [loss[1].outcome, loss[2].outcome] })
    await flush()
    expect(queue.getSnapshot().state!.status).toBe('lost')
    expect(queue.getSnapshot().pendingCount).toBe(0)
  })

  it('retries a lost response using the same UUID and revision, without predicting a second lost life', async () => {
    const first = deferred<BatchResponse>(),
      retry = deferred<BatchResponse>()
    const client = {
      batch: vi
        .fn<(state: GameState, actions: QueuedAction[]) => Promise<BatchResponse>>()
        .mockReturnValueOnce(first.promise)
        .mockReturnValueOnce(retry.promise),
      session: vi.fn(async () => blocked.before),
    }
    const queue = new ActionQueue(client)
    queue.setState(blocked.before)
    queue.enqueue('tap', 1)
    first.reject(Object.assign(new Error('Disconnected'), { status: 0 }))
    await flush()
    expect(queue.getSnapshot().error).toContain('Saving is paused')
    expect(queue.getSnapshot().state!.lives_remaining).toBe(2)
    expect(queue.getSnapshot().pendingCount).toBe(1)
    queue.retry()
    expect(client.batch.mock.calls[1]).toEqual(client.batch.mock.calls[0])
    retry.resolve({ state: blocked.after, outcomes: [blocked.outcome] })
    await flush()
    expect(queue.getSnapshot().state!.lives_remaining).toBe(2)
    expect(queue.getSnapshot().pendingCount).toBe(0)
  })

  it('restores pending moves after reload and skips the already-committed prefix', async () => {
    const storage = memoryStorage()
    const request = deferred<BatchResponse>()
    const originalClient = {
      batch: vi
        .fn<(state: GameState, actions: QueuedAction[]) => Promise<BatchResponse>>()
        .mockReturnValue(request.promise),
      session: vi.fn(async () => win[0].before),
    }
    const original = new ActionQueue(originalClient, storage)
    original.setState(win[0].before)
    original.enqueue('tap', 2)
    original.enqueue('tap', 1)
    const firstID = originalClient.batch.mock.calls[0][1][0].action_id
    const saved = JSON.parse(storage.getItem(`amaze-go-pending-v1:${win[0].before.session_id}`)!)
    const pendingID = saved.actions[1].action_id
    const server = { ...win[0].after, processed_action_ids: [firstID] }
    const restoredClient = {
      batch: vi.fn(async (_state: GameState, _actions: QueuedAction[]) => ({
        state: win[1].after,
        outcomes: [win[1].outcome],
      })),
      session: vi.fn(async () => server),
    }
    const restored = new ActionQueue(restoredClient, storage)
    restored.setState(server)
    expect(restored.getSnapshot().state!.status).toBe('won')
    expect(restoredClient.batch.mock.calls[0][0].revision).toBe(1)
    expect(restoredClient.batch.mock.calls[0][1]).toEqual([
      { type: 'tap', arrow_id: 1, action_id: pendingID },
    ])
    await flush()
    expect(restored.getSnapshot().pendingCount).toBe(0)
    expect(storage.getItem(`amaze-go-pending-v1:${server.session_id}`)).toBeNull()
  })

  it('rolls back unconfirmed predictions after a conflicting change from another tab', async () => {
    const client = {
      batch: vi.fn(async (_state: GameState, _actions: QueuedAction[]): Promise<BatchResponse> => {
        throw Object.assign(new Error('Stale revision'), { status: 409 })
      }),
      session: vi.fn(async () => blocked.after),
    }
    const queue = new ActionQueue(client)
    queue.setState(win[0].before)
    queue.enqueue('tap', 2)
    queue.enqueue('tap', 1)
    expect(queue.getSnapshot().state!.status).toBe('won')
    await flush()
    expect(queue.getSnapshot().state).toEqual(blocked.after)
    expect(queue.getSnapshot().pendingCount).toBe(0)
    expect(queue.getSnapshot().error).toContain('latest saved state')
    queue.retry()
    expect(queue.getSnapshot().error).toBeNull()
  })
})
