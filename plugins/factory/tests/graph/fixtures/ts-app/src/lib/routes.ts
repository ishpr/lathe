import { Route } from 'foldkit'
import { defineRouteUnion } from 'foldkit/route'

export const AppRoute = defineRouteUnion({
  Runs: {},
  RunDetail: { id: Route.string },
  NotFound: {},
})
