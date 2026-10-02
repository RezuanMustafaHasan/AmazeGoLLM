export function nextTurnDelay(delay: number, retryAt: number | null | undefined, now: number) {
  return Math.max(delay, (retryAt ?? 0) * 1000 - now, 0)
}

export function canResume(status: string, inFlight: boolean) {
  return status !== 'completed' && !inFlight
}
