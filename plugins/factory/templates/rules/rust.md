---
paths:
  - "**/*.rs"
---
# Rust

<!-- sdlc:if routes -->
- Auth is an extractor or a tower layer, not a check inside each handler.
<!-- sdlc:end -->
- No `static mut`, no process-global `Mutex<HashMap>` that requests fill; state goes to a store
  behind a trait with an in-memory implementation for tests. Config loaded once and never mutated
  may be marked `// state: ok <reason>`.
- `unwrap()` and `expect()` only in tests and at startup; handlers return `Result` with a typed error.
- Tests: `#[test]` per behaviour, `assert_eq!` on values, at least one `is_err()` / `#[should_panic]`
  case per module, no sleeps.
- `rustfmt` on save (the post-edit hook runs it); `cargo clippy` in the ratchet.
