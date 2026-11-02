// Every backend URL the programs use. Commands call these functions; nothing else builds a URL.
export const API_ROOT = '/api'

export const runsUrl = (): string => `${API_ROOT}/runs`

export const runUrl = (id: string): string => `${API_ROOT}/runs/${id}`

export function nodesUrl(): string {
  return `${API_ROOT}/nodes`
}

export interface RunSummary {
  id: string
}

export const getJson = (url: string) => fetch(url).then(response => response.json())
