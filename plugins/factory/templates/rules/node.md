---
paths:
  - "**/*.ts"
  - "**/*.tsx"
  - "**/*.js"
  - "**/*.mjs"
---
# Node / TypeScript

- Auth and validation live in middleware applied at the router level, not per handler; a handler
  never trusts `req.body` for identity.
- No module-scope `let` or empty `Map`/`Set`/`{}` that a request fills; state goes to Redis or the
  database behind an interface with a fake.
- Validate every input at the boundary (zod or the project's schema library); types are not
  validation.
- Tests: one `it`/`test` per behaviour, at least one `expect` that checks a value (not
  `toBeDefined`), a negative case per file (`toThrow`, `rejects`, a 4xx/5xx), fake timers for time.
- Lint and format with eslint and prettier (the post-edit hook runs them when installed); `tsc --noEmit` for types.
