import { Effect, Schema } from 'effect'
import { Command } from 'foldkit'

import { saveSettings } from '../../lib/api/endpoints'
import { Message } from './message'

export const SaveSettings = Command.define('SaveSettings', {
  args: { theme: Schema.String },
  messages: [Message.SucceededSaveSettings],
  execute: ({ theme }) =>
    saveSettings(theme).pipe(
      Effect.map(() => Message.SucceededSaveSettings()),
      Effect.orElseSucceed(() => Message.SucceededSaveSettings()),
    ),
})
