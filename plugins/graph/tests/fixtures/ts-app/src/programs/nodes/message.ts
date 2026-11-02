import { Schema } from 'effect'
import { defineMessageUnion } from 'foldkit/message'

export const Message = defineMessageUnion({
  ClickedDelete: { id: Schema.String },
  SucceededLoadNodes: { nodes: Schema.Array(Schema.String) },
  SucceededDeleteNode: {},
})
export type Message = typeof Message.Type
