import { Update } from 'foldkit'

import { FetchRuns } from './commands'
import { Message } from './message'
import type { Model } from './model'

export const update = (model: Model, message: Message) =>
  Message.match<Update.Return<Model, Message>>(message, {
    ClickedRefresh: () => ({ model, commands: [FetchRuns()] }),
    ClickedSelect: ({ id }) => ({ model: { ...model, selected: id } }),
    SucceededFetchRuns: ({ runs }) => ({ model: { ...model, runs } }),
    FailedFetchRuns: () => ({ model }),
  })
