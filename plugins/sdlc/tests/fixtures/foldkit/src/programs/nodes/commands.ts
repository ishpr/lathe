import { Effect, Schema } from 'effect'
import { Command } from 'foldkit'

import { getNode, listNodes } from '../../lib/api/endpoints'
import { Message } from './message'

export const LoadNodes = Command.define('LoadNodes', {
  messages: [Message.SucceededLoadNodes, Message.FailedLoadNodes],
  execute: listNodes().pipe(
    Effect.map(nodes => Message.SucceededLoadNodes({ nodes })),
    Effect.catchAll(error => Effect.succeed(Message.FailedLoadNodes({ reason: String(error) }))),
  ),
})

export const LoadNode = Command.define('LoadNode', {
  args: { id: Schema.String },
  messages: [Message.SucceededLoadNodes, Message.FailedLoadNodes],
  execute: ({ id }) =>
    Effect.tryPromise(() => fetch(`/api/nodes/${id}`).then(r => r.json())).pipe(
      Effect.map(node => Message.SucceededLoadNodes({ nodes: [node] })),
      Effect.catchAll(() => getNode(id).pipe(Effect.map(node => Message.SucceededLoadNodes({ nodes: [node] })))),
    ),
})

// Planted: calls a backend URL that no function in the api table exposes.
export const ArchiveNode = Command.define('ArchiveNode', {
  args: { id: Schema.String },
  messages: [Message.SucceededArchiveNode, Message.FailedLoadNodes],
  execute: ({ id }) =>
    Effect.tryPromise(() =>
      fetch('/api/nodes/archive', {
        method: 'POST',
        body: JSON.stringify({ id }),
      }),
    ).pipe(
      Effect.map(() => Message.SucceededArchiveNode({ id })),
      Effect.catchAll(error => Effect.succeed(Message.FailedLoadNodes({ reason: String(error) }))),
    ),
})
