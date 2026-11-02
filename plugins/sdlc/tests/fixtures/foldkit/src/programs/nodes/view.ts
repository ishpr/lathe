import type { Html, HtmlBuilder } from 'foldkit/html'

import { Message } from './message'
import { Model } from './model'

export const view = (model: Model, h: HtmlBuilder<Message>): Html =>
  h.section(
    [h.AriaLabel('Nodes')],
    [
      h.button([h.OnClick(Message.ClickedRefresh())], ['Refresh']),
      h.input([
        h.AriaLabel('Filter nodes'),
        h.Value(model.query),
        h.OnInput(query => Message.ChangedQuery({ query })),
      ]),
      h.ul(
        [],
        model.nodes.map(node =>
          h.li(
            [h.Role('button'), h.AriaLabel(`Open ${node.name}`), h.OnClick(Message.ClickedRow({ id: node.id }))],
            [node.name],
          ),
        ),
      ),
      // Planted: an interactive element a scene test can reach only by text('Archive').
      h.div(
        [h.Class('archive'), h.OnClick(Message.ClickedArchive({ id: 'all' }))],
        ['Archive'],
      ),
    ],
  )
