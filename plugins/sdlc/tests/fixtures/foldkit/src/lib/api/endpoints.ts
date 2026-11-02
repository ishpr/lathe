// The API call table: every backend call a program makes goes through one of these functions.
import { Effect } from 'effect'

const json = (url: string, init?: RequestInit) =>
  Effect.tryPromise(() => fetch(url, init).then(response => response.json()))

export const listNodes = () => json('/api/nodes')

export const getNode = (id: string) => json(`/api/nodes/${id}`)

export function createNode(name: string) {
  return json('/api/nodes', { method: 'POST', body: JSON.stringify({ name }) })
}

export const saveSettings = (theme: string) =>
  json('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({ theme }),
  })
