import { describe, expect, test } from 'vitest'
import * as Story from 'foldkit/story'

import { LoadNodes } from './commands'
import { Message } from './message'
import { init } from './model'
import { update } from './update'

const { model: initial } = init()

describe('nodes', () => {
  test('refresh loads the nodes', () => {
    Story.story(
      update,
      Story.given(initial),
      Story.message(Message.ClickedRefresh()),
      Story.Command.expectExact(LoadNodes),
      Story.Command.resolve(LoadNodes, Message.SucceededLoadNodes({ nodes: [{ id: 'n1', name: 'One' }] })),
      Story.model(model => expect(model.nodes).toHaveLength(1)),
    )
  })

  test('a failed load stops the spinner', () => {
    Story.story(
      update,
      Story.given({ ...initial, isLoading: true }),
      Story.message(Message.FailedLoadNodes({ reason: 'offline' })),
      Story.model(model => expect(model.isLoading).toBe(false)),
    )
  })

  test('selecting and filtering', () => {
    Story.story(
      update,
      Story.given(initial),
      Story.message(Message.ClickedRow({ id: 'n1' })),
      Story.message(Message.ChangedQuery({ query: 'on' })),
      // Story.message(Message.ClickedArchive({ id: 'n1' })) in a comment does not count.
      Story.model(model => expect(model.query).toBe('on')),
    )
  })

  test('archived nodes leave the list', () => {
    Story.story(
      update,
      Story.given({ ...initial, nodes: [{ id: 'n1', name: 'One' }] }),
      Story.message(Message.SucceededArchiveNode({ id: 'n1' })),
      Story.model(model => expect(model.nodes).toHaveLength(0)),
    )
  })
})
