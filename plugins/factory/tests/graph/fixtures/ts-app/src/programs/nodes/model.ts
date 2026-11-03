import { Schema } from 'effect'

export const Model = Schema.Struct({ nodes: Schema.Array(Schema.String) })
export type Model = typeof Model.Type
