export const formatCost = (usd: number): string => `$${usd.toFixed(2)}`

export function clamp(value: number, low: number, high: number): number {
  return Math.min(Math.max(value, low), high)
}

const internalOnly = (x: number) => x * 2

export const LIMIT = 50
