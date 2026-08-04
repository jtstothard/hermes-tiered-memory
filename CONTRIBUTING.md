# Contributing to hermes-tiered-memory

Thanks for your interest in contributing! This is a small project, so the
process is lightweight.

## Getting started

```bash
git clone https://github.com/jtstothard/hermes-tiered-memory.git
cd hermes-tiered-memory
python3 -m pip install -e '.[test]'
python3 -m pytest -q
```

Requirements: Python `>=3.11,<3.14`.

## Development workflow

1. **Fork and branch** from `main`. Use a descriptive branch name
   (`fix/...`, `feat/...`, `docs/...`).
2. **Write tests.** New behavior needs test coverage. The suite is in
   `tests/unit/` and `tests/contract/`.
3. **Keep quality gates green.** Before pushing:
   ```bash
   ruff check hermes_tiered_memory tests
   mypy hermes_tiered_memory
   black --check hermes_tiered_memory tests
   python3 -m pytest -q
   ```
4. **Open a pull request** against `main` with a clear description of what
   changed and why.

## Commit conventions

Use [Conventional Commits](https://www.conventionalcommits.org/):
`fix:`, `feat:`, `docs:`, `ci:`, `chore:`, `test:`.

## Scope

v1.0.0's public surface is intentionally conservative:
- **Stable:** `tiered_memory_store`, `tiered_memory_search`,
  `tiered_memory_delete`, the public configuration schema, and the
  `[mnemosyne]` / `[hindsight]` pip extras.
- **Private:** the L1/L2 adapter ABCs and the protected-config wire format.

Changes to the stable surface are a semver-major bump. Changes to private
internals are not.

## Reporting issues

- **Bugs:** open a GitHub issue with reproduction steps, expected vs actual
  behavior, and `pip show hermes-tiered-memory` output.
- **Security:** see [SECURITY.md](SECURITY.md) — do not open a public issue.

## Code of conduct

Be respectful. Be constructive. We're all here because we want better memory
tooling for Hermes.
