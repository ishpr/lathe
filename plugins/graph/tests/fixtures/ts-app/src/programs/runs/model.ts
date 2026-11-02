import { Schema } from 'effect'

export const Model = Schema.Struct({ runs: Schema.Array(Schema.String), selected: Schema.NullOr(Schema.String) })
export type Model = typeof Model.Type

export const init = (): Model => ({ runs: [], selected: null })
