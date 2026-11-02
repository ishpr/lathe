import { Scene } from 'foldkit/scene'
import { describe, it } from 'vitest'

import { init } from './model'
import { update } from './update'
import { view } from './view'

describe('runs scene', () => {
  it('shows the refresh button', () => {
    Scene.scene({ update, view }, Scene.with(init()), Scene.expect(Scene.role('button', { name: 'Refresh' })).toExist())
  })
})
