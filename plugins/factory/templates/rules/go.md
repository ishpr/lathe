---
paths:
  - "**/*.go"
---
# Go

<!-- sdlc:if routes -->
- Auth is middleware on the mux or router group, not a check inside each handler.
<!-- sdlc:end -->
- No package-level `var` maps, slices or `sync.Map` that handlers write to; pass a store interface
  into the handler with a fake for tests. Registries filled in `init()` and never mutated may be
  marked `// state: ok <reason>`.
- Every error is handled or returned; `_ = err` and empty `if err != nil {}` are bugs.
- Contexts carry deadlines on every outbound call.
- Tests: table-driven, `t.Run` per case, a `wantErr` case in every table, `t.Fatal` for setup
  failures, `t.Error` for assertions, no sleeps.
- `gofmt` on save (the post-edit hook runs it); `go vet` and `go build` in the ratchet.
