import { Schema } from 'effect'
import { defineMessageUnion } from 'foldkit/message'

import { Node } from './model'

export const Message = defineMessageUnion({
  ClickedRefresh: {},
  SucceededLoadNodes: { nodes: Schema.Array(Node) },
  FailedLoadNodes: { reason: Schema.String },
  ClickedRow: { id: Schema.String },
  ChangedQuery: { query: Schema.String },
  // Planted: no story test sends this tag.
  ClickedArchive: { id: Schema.String },
  SucceededArchiveNode: { id: Schema.String },
})
export type Message = typeof Message.Type
