import { Schema } from 'effect'
import { defineMessageUnion } from 'foldkit/message'

export const Message = defineMessageUnion({
  ChangedTheme: { theme: Schema.Literal('dark', 'light') },
  ClickedSave: {},
  SucceededSaveSettings: {},
})
export type Message = typeof Message.Type
