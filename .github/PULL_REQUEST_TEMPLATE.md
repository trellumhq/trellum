<!--
Contributions are accepted under the repository's AGPL-3.0-only licence. There is no CLA and no
DCO — nothing to sign, no sign-off trailer needed. See CONTRIBUTING.md.
-->

## What and why

<!-- What changes, and the reason. The diff already shows what; explain why. -->

## How it was verified

<!--
Not "tests pass" — what did you actually run, and what did you observe?

    cd demo
    python -m pytest ../testing -q -m "not slow"
    python -m trellum.run --all --no-serve
-->

## Checklist

- [ ] Tests added or updated, and the suite passes from `demo/`
- [ ] Documented behaviour changes are reflected in the docs, in this PR
- [ ] No new `RawHTML` where an existing component would do
- [ ] No changes to a Tier 1 surface in `docs/COMPATIBILITY.md` — or, if there are, a
      deprecation path is included and called out below
- [ ] No per-file licence headers added (see `docs/LICENSING.md`)

## Anything reviewers should look at closely

<!-- The part you are least sure about. Naming it makes review faster and better. -->
