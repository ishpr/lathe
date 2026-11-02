import { test } from 'vitest'
import * as Scene from 'foldkit/scene'

import { init } from './model'
import { update } from './update'
import { view } from './view'

test('the form is reachable by label and role', () => {
  Scene.scene(
    { update, view },
    Scene.given(init().model),
    Scene.expect(Scene.label('Theme')).toExist(),
    Scene.expect(Scene.role('button', { name: 'Save' })).toExist(),
  )
})
