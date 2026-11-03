import { Option } from 'effect'
import { Update } from 'foldkit'
import { modifyFields } from 'foldkit/struct'

import { ArchiveNode, LoadNodes } from './commands'
import { Message } from './message'
import { Model } from './model'

export const update = (model: Model, message: Message) =>
  Message.match<Update.Return<Model, Message>>(message, {
    ClickedRefresh: () => ({ model: modifyFields(model, { isLoading: () => true }), commands: [LoadNodes()] }),
    SucceededLoadNodes: ({ nodes }) => ({ model: modifyFields(model, { nodes: () => nodes, isLoading: () => false }) }),
    FailedLoadNodes: () => ({ model: modifyFields(model, { isLoading: () => false }) }),
    ClickedRow: ({ id }) => ({ model: modifyFields(model, { maybeSelectedId: () => Option.some(id) }) }),
    ChangedQuery: ({ query }) => ({ model: modifyFields(model, { query: () => query }) }),
    ClickedArchive: ({ id }) => ({ model, commands: [ArchiveNode({ id })] }),
    SucceededArchiveNode: ({ id }) => ({
      model: modifyFields(model, { nodes: nodes => nodes.filter(node => node.id !== id) }),
    }),
  })
