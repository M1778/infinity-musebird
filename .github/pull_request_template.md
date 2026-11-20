## What this changes

<!-- One paragraph. Link the issue if there is one. -->

## Checklist

- [ ] `ruff check .` passes
- [ ] `pyright` passes
- [ ] `pytest` passes (mock driver only; no account needed)
- [ ] New Muse-specific selectors/URLs/cookies are in `src/musebird/upstream/muse.py` only
- [ ] `docs/HARNESS_NOTES.md` updated if a harness config format changed (with today's date and a doc URL)
- [ ] No secrets, cookies, tokens, or real session data in the diff or fixtures
- [ ] Anything Muse returns is still treated as untrusted (validated paths, verified hashes, no execution)
- [ ] Installer changes are covered by fixture tests (install, idempotency, uninstall)

## Notes for reviewers

<!-- Tradeoffs, follow-ups, anything intentionally left out. -->
