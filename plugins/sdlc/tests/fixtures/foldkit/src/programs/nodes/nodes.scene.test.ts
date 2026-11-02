import { test } from 'vitest'
import * as Scene from 'foldkit/scene'

import { init } from './model'
import { update } from './update'
import { view } from './view'

test('the refresh button is reachable by role', () => {
  Scene.scene(
    { update, view },
    Scene.given(init().model),
    Scene.expect(Scene.role('button', { name: 'Refresh' })).toExist(),
    Scene.expect(Scene.label('Filter nodes')).toExist(),
  )
})
