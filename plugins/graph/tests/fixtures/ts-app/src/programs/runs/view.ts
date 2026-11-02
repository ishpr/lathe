import type { Document, HtmlBuilder } from 'foldkit/html'

import { Message } from './message'
import type { Model } from './model'

export const view = (model: Model, h: HtmlBuilder<Message>): Document => ({
  title: 'Runs',
  body: h.div([], [h.button([h.OnClick(Message.ClickedRefresh())], ['Refresh']), h.p([], [String(model.runs.length)])]),
})

const rowLabel = (id: string) => `run ${id}`
