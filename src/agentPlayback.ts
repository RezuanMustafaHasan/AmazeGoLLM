export function nextTurnDelay(
  delay: number,
  retryAt: number | null | undefined,
  now: number,
  leaseExpiresAt?: number | null,
) {
  return Math.max(delay, (retryAt ?? 0) * 1000 - now, (leaseExpiresAt ?? 0) * 1000 - now, 0)
}

export function clientRetryDelay(failures: number) {
  return Math.min(5000 * 2 ** (Math.min(Math.max(failures, 1), 5) - 1), 60000)
}

export function canResume(status: string, inFlight: boolean) {
  return status !== 'completed' && !inFlight
}
