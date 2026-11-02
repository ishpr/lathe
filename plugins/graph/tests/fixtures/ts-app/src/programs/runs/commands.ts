import { Effect } from 'effect'
import { Command } from 'foldkit'

import { getJson, runsUrl } from '@/lib/api/endpoints'
import { Message } from './message'

export const FetchRuns = Command.define('FetchRuns', {
  messages: [Message.SucceededFetchRuns, Message.FailedFetchRuns],
  execute: Effect.tryPromise(() => getJson(runsUrl())).pipe(
    Effect.map(runs => Message.SucceededFetchRuns({ runs })),
    Effect.catch(error => Effect.succeed(Message.FailedFetchRuns({ reason: String(error) }))),
  ),
})
