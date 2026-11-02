import { Schema } from 'effect'
import { defineMessageUnion } from 'foldkit/message'

export const Message = defineMessageUnion({
  ClickedRefresh: {},
  ClickedSelect: { id: Schema.String },
  SucceededFetchRuns: { runs: Schema.Array(Schema.String) },
  FailedFetchRuns: { reason: Schema.String },
})
export type Message = typeof Message.Type
