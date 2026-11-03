import type { Html, HtmlBuilder } from 'foldkit/html'

import { Message } from './message'
import { Model } from './model'

export const view = (model: Model, { form, label, select, option, button, OnChange, OnClick, Id, For, Value, Disabled }: HtmlBuilder<Message>): Html =>
  form(
    [],
    [
      label([For('theme')], ['Theme']),
      select(
        [Id('theme'), Value(model.theme), OnChange(theme => Message.ChangedTheme({ theme: theme === 'light' ? 'light' : 'dark' }))],
        [option([Value('dark')], ['Dark']), option([Value('light')], ['Light'])],
      ),
      button([OnClick(Message.ClickedSave()), Disabled(model.isSaving)], ['Save']),
    ],
  )
