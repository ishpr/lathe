import { formatCost } from '@/lib/format'

export default function RunsPage() {
  return <p>{formatCost(1)}</p>
}

export async function loadRuns() {
  const response = await fetch('/api/runs')
  return response.json()
}
