import { Story } from 'foldkit/story'
import { describe, it } from 'vitest'

import { FetchRuns } from './commands'
import { Message } from './message'
import { init } from './model'
import { update } from './update'

describe('runs story', () => {
  it('selects a run', () => {
    Story.story(
      update,
      Story.with(init()),
      Story.message(Message.ClickedSelect({ id: 'r1' })),
      Story.message(Message.SucceededFetchRuns({ runs: ['r1'] })),
      Story.message(Message.FailedFetchRuns({ reason: 'offline' })),
    )
  })
})
