# Rules for every change (SDLC)

- Read the contract before touching code. Edit only inside its `allowed_paths`; a path outside it
  is a contract change first, a code change second.
- New state goes behind an interface with a real store and an in-memory fake. The fake is for tests
  only and takes an injected clock.
<!-- sdlc:if routes -->
- No module-level mutable state on a request path.
<!-- sdlc:end -->
- Every store that talks to a network declares its failure mode (fail open with a metric, or fail
  closed) and has a test for it.
- Acceptance criteria become test names. A test asserts behaviour (status, body, headers, stored
  state), never that something exists. Every test file has a negative case.
- Delete what you replace. Nothing is kept "for later".
- Secrets come from the environment or a secrets manager; never from a file in the repo and never
  read inside a request handler.
- A commit message describes the diff: subject under 72 characters as `<type>(<scope>): <what changed>`,
  body says why, claims nothing the diff does not do.
- Ask before, never during. Every question is asked once, in one batch, when `/factory:design`
  writes the contract; the contract records each answer as a Decision Value. After that nothing
  asks: an ambiguity is settled by the contract's `## Defaults when ambiguous` and recorded in
  `.claude/work/<slug>/assumptions.md`, which the reviewer reads. An assumption never contradicts a
  Decision; what no default may settle (a path or capability the contract lacks, a wrong frozen test,
  money, security, data loss) is an escalation to `.claude/work/<slug>/questions.md`, not a prompt.
<!-- sdlc:if migrate -->
- Schema changes are additive first: expand (new nullable column or table), migrate (move readers
  and writers), contract (remove the old one) as separate changes. Every model change ships with a
  migration in the same PR. Never run a downgrade, stamp, drop or truncate against a shared database.
<!-- sdlc:end -->
