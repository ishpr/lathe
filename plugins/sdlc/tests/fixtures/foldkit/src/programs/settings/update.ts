import { Update } from 'foldkit'
import { modifyFields } from 'foldkit/struct'

import { SaveSettings } from './commands'
import { Message } from './message'
import { Model } from './model'

export const update = (model: Model, message: Message) =>
  Message.match<Update.Return<Model, Message>>(message, {
    ChangedTheme: ({ theme }) => ({ model: modifyFields(model, { theme: () => theme }) }),
    ClickedSave: () => ({
      model: modifyFields(model, { isSaving: () => true }),
      commands: [SaveSettings({ theme: model.theme })],
    }),
    SucceededSaveSettings: () => ({ model: modifyFields(model, { isSaving: () => false }) }),
  })
