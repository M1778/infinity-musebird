# Contributing

## Setup

```bash
git clone https://github.com/M1778/infinity-musebird
cd infinity-musebird
uv venv
uv pip install -e '.[all]'
uv pip install pytest pytest-asyncio ruff pyright
.venv/bin/python -m pytest
```

No Muse account is needed: the whole test suite runs against the `mock` driver.
Live tests are marked `@pytest.mark.live` and are skipped by default.

## Ground rules

1. **Muse-specific facts go in `src/musebird/upstream/muse.py`.** URLs, cookie
   names, selectors, sentinels. If a PR touches a selector anywhere else, it is
   probably wrong.
2. **Everything Muse returns is untrusted.** New code paths that read remote
   content must validate paths, verify hashes, and never execute anything.
3. **No secrets in tests or fixtures.** Use `hatch_sess=FAKE`, `ghp_` + `a`*36, etc.
4. **The installer is Python.** `install.sh` and `install.ps1` only bootstrap; all
   logic and all tests live in `musebird.installer`.
5. **Jobs are async.** Do not add a tool that blocks for minutes; add a job state.

## Checks before a PR

```bash
./scripts/check.sh
```

That runs ruff, pyright, a wheel build, the full test suite, and the repository
secret scan — the same set as CI. `./scripts/check.sh --fast` skips the wheel build.

The CI runs the same on Linux, macOS and Windows. If you change an installer
adapter, update `docs/HARNESS_NOTES.md` with the docs URL and date you verified it
against.

## Adding a harness adapter

Implement `Adapter` in `src/musebird/installer/adapters.py`, add a detector in
`detect.py`, add the skill directory to `skill_dirs()`, and add a fixture test in
`tests/test_installer.py` covering install, idempotency, and uninstall. The test
must not require the harness binary.
