import { expect, test } from 'vitest'
import * as Story from 'foldkit/story'

import { SaveSettings } from './commands'
import { Message } from './message'
import { init } from './model'
import { update } from './update'

test('saving the theme', () => {
  Story.story(
    update,
    Story.given(init().model),
    Story.message(Message.ChangedTheme({ theme: 'light' })),
    Story.message(Message.ClickedSave()),
    Story.Command.resolve(SaveSettings, Message.SucceededSaveSettings()),
    Story.model(model => expect(model.isSaving).toBe(false)),
  )
})
