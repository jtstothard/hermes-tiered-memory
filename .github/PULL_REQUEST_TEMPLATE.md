## Summary

Brief description of what this PR changes and why.

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (changes the stable public surface)
- [ ] Documentation only

## Testing

- [ ] `ruff check hermes_tiered_memory tests` passes
- [ ] `mypy hermes_tiered_memory` passes
- [ ] `black --check hermes_tiered_memory tests` passes
- [ ] `python3 -m pytest -q` passes
- [ ] New tests added for new behavior (if applicable)

## Checklist

- [ ] Changes to the stable public surface (`tiered_memory_*` tools, config
      schema, pip extras) are noted as semver-relevant
- [ ] No secrets, credentials, or estate-identifying paths introduced
- [ ] CHANGELOG.md updated if user-facing
