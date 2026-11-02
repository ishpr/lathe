import { Runtime } from 'foldkit'

import { Message } from './programs/nodes/message'
import { Model, init } from './programs/nodes/model'
import { update } from './programs/nodes/update'
import { view } from './programs/nodes/view'

// Planted: DevTools shown to every production user, with dispatch enabled.
const application = Runtime.makeApplication({
  Model,
  init,
  update,
  view,
  container: document.getElementById('root'),
  devTools: {
    show: 'Always',
    Message,
  },
})

Runtime.run(application)
