import { Schema } from 'effect'
import { defineSubmodel } from 'foldkit/submodel'

export const NodeRow = defineSubmodel('NodeRow', {
  model: Schema.Struct({ id: Schema.String }),
})
