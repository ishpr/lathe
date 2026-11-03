import { Effect, Schema } from 'effect'
import * as Api from '@/lib/api/endpoints'
import { Command } from 'foldkit'

import { deleteNodeUrl, nodesUrl } from '@/lib/api/endpoints'
import { Message } from './message'

export const LoadNodes = Command.define('LoadNodes', {
  messages: [Message.SucceededLoadNodes],
  execute: Effect.promise(() => fetch(nodesUrl())).pipe(Effect.as(Message.SucceededLoadNodes({ nodes: [] }))),
})

export const DeleteNode = Command.define('DeleteNode', {
  args: { id: Schema.String },
  messages: [Message.SucceededDeleteNode],
  execute: ({ id }) => Effect.promise(() => fetch(deleteNodeUrl(id), { method: 'DELETE' })).pipe(Effect.as(Message.SucceededDeleteNode())),
})

export const RefreshRuns = Command.define('RefreshRuns', {
  messages: [Message.SucceededLoadNodes],
  execute: Effect.promise(() => Api.getJson(Api.runUrl('latest'))).pipe(Effect.as(Message.SucceededLoadNodes({ nodes: [] }))),
})
