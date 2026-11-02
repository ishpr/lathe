---
name: feature
description: Run a ticket end to end (contract, tests, implement, verify) with code-graph impact at the start and PR evidence recorded at the end, stopping at every human decision. Use only when the user explicitly asks to run a whole ticket as a feature. Human-invoked.
argument-hint: "<ticket text>"
disable-model-invocation: true
allowed-tools: Skill, Read, Glob, Grep
---
Run a ticket end to end. The ticket text: $ARGUMENTS

`sdlc:feature` owns the stages and already stops at every human decision. Do not skip,
shorten or reorder its stops, and do not make decisions for the human.

1. **Impact first (optional).** If the skill `graph:impact` is available, run it on the
   repo paths the ticket names, as `/factory:ticket` step 1 does, and keep the affected routes,
   tests and owners. If it is missing, fails, or the ticket names no paths, say so in one line
   and go on.
2. **Stages.** Invoke the skill `sdlc:feature` with the ticket text unchanged and no
   other argument. Give it the impact result for the contract's allowed paths. Follow its stops:
   a `REJECTED` result, the Decisions stop, and the final stop before `/sdlc:ship`. When
   it stops for a human, relay what it needs and end your turn; resume only when the human
   answers.
3. **Evidence at the end.** Only once a PR exists and you have its real URL (the human ran
   `/sdlc:ship`, or the URL is in the stage output): if the skill `graph:feed`
   is available, invoke it with the contract slug, kind `pr_url` and a payload built from that URL.
   Never invent a URL or payload. If there is no PR yet, say "no pr_url to record yet" and
   stop. If `graph:feed` is missing or fails, say so and carry on.
4. **Close.** Print the Evidence section and the PR URL if any. Remind the human that
   `/sdlc:ship` and merging are theirs.

Never edit outside the contract's allowed paths, never commit without a verify stamp, never
update the baseline, never unfreeze tests. If a stage cannot complete, report where it stopped
and what it needs.
