import { DeleteNode, LoadNodes } from './command'
import { Message } from './message'
import type { Model } from './model'

export const update = (model: Model, message: Message) =>
  Message.match(message, {
    ClickedDelete: ({ id }) => ({ model, commands: [DeleteNode({ id })] }),
    SucceededLoadNodes: ({ nodes }) => ({ model: { nodes } }),
    SucceededDeleteNode: () => ({ model, commands: [LoadNodes()] }),
  })
