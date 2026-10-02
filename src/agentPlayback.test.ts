import { describe, expect, it } from 'vitest'
import { canResume, nextTurnDelay } from './agentPlayback'

describe('saved agent playback', () => {
  it('waits for the gateway cooldown even when autoplay has no configured delay', () => {
    expect(nextTurnDelay(0, 130, 100000)).toBe(30000)
    expect(nextTurnDelay(1000, 130, 135000)).toBe(1000)
    expect(nextTurnDelay(3000, null, 100000)).toBe(3000)
  })

  it('resumes paused and stopped sessions while protecting completed and in-flight sessions', () => {
    expect(canResume('paused', false)).toBe(true)
    expect(canResume('stopped', false)).toBe(true)
    expect(canResume('error', false)).toBe(true)
    expect(canResume('completed', false)).toBe(false)
    expect(canResume('paused', true)).toBe(false)
  })
})
