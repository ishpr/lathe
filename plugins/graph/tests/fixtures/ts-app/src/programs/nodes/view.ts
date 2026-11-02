import type { Html, HtmlBuilder } from 'foldkit/html'

import { Message } from './message'
import type { Model } from './model'

export function nodeList(model: Model, h: HtmlBuilder<Message>): Html {
  return h.ul([], model.nodes.map(id => h.li([h.OnClick(Message.ClickedDelete({ id }))], [id])))
}
