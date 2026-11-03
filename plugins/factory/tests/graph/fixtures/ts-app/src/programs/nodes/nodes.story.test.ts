import { Story } from 'foldkit/story'

import { Message } from './message'
import { update } from './update'

Story.story(
  update,
  Story.message(Message.ClickedDelete({ id: 'n1' })),
  Story.message({ _tag: 'SucceededDeleteNode' }),
  Story.message(Message.SucceededLoadNodes({ nodes: [] })),
)
