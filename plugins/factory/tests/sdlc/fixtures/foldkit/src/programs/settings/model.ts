import { Schema } from 'effect'

export const Model = Schema.Struct({
  theme: Schema.Literal('dark', 'light'),
  isSaving: Schema.Boolean,
  isPasswordVisible: Schema.Boolean,
  maxTokens: Schema.Number,
})
export type Model = typeof Model.Type

export const init = (): { model: Model } => ({
  model: { theme: 'dark', isSaving: false, isPasswordVisible: false, maxTokens: 1000 },
})
