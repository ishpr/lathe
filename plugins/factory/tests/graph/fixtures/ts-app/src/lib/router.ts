import { nodeList } from '../programs/nodes/view'
import { view as runsView } from '../programs/runs/view'
import { AppRoute } from './routes'

export const pickView = (route: AppRoute.Type) =>
  AppRoute.match(route, {
    Runs: () => runsView,
    RunDetail: ({ id }) => nodeList,
    NotFound: () => null,
  })
