import { defineMessageUnion } from 'foldkit/message'

// Copied by the new-program script; not a program, so the checkers skip it.
export const Message = defineMessageUnion({
  Clicked__Program__: {},
})
export type Message = typeof Message.Type
