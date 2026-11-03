import { Option, Redacted, Schema } from 'effect'

export const Node = Schema.Struct({ id: Schema.String, name: Schema.String })
export type Node = typeof Node.Type

export const Model = Schema.Struct({
  nodes: Schema.Array(Node),
  query: Schema.String,
  isLoading: Schema.Boolean,
  maybeSelectedId: Schema.Option(Schema.String),
  // Planted: a credential in the Model, which DevTools records and the MCP relay can read.
  apiKey: Schema.String,
  sessionToken: Schema.Redacted(Schema.String),
  tokenCount: Schema.Number,
})
export type Model = typeof Model.Type

export const init = (): { model: Model } => ({
  model: {
    nodes: [],
    query: '',
    isLoading: false,
    maybeSelectedId: Option.none(),
    apiKey: '',
    sessionToken: Redacted.make(''),
    tokenCount: 0,
  },
})
