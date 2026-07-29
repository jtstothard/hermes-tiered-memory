# PROTOTYPE — 08 acceptance handoff

Throwaway local acceptance harness. **Do not deploy or copy into production.** It uses only Python stdlib fakes and a temporary/file-backed JSON outbox. It does not claim live Hermes, Mnemosyne, Hindsight, network, credential, or API compatibility.

## Pre-registered hypothesis and decision rule

- **Question:** Does the locked end-to-end contract from tickets 01–07 compose coherently enough for a harden + publish successor?
- **Hypothesis:** A composite provider can preserve cache-first behavior, durable asynchronous promotion, deterministic revisions, metadata provenance, isolation, and failure recovery without coupling foreground L1 operations to L2 availability.
- **Disconfirmation:** any contract case fails, duplicate tool namespace appears, L2 outage loses a foreground L1 write, replay duplicates durable identity, or profile/bank state crosses boundaries.
- **Decision rule:** PASS within the tested fake boundary only if all contract tests and the independent evidence check pass.

`run_evidence.py` writes `evidence.json`; this is generated throwaway evidence.
The evidence records a `source_revision` and binds the runner, verifier,
harness, and tests by SHA-256. The revision may be an ancestor of the current
`HEAD` (for example, later documentation-only commits are allowed), but the
verifier requires `git merge-base --is-ancestor <source_revision> HEAD` and
rejects every dirty worktree path except the generated `evidence.json` itself.

Run the checks from a clean checkout of the repository (a temporary checkout is
recommended when verifying an uncommitted change):

```bash
cd /home/hermes/hermes-tiered-memory
python3 -m unittest discover -s prototype-acceptance-handoff/tests -v
python3 prototype-acceptance-handoff/run_evidence.py
python3 prototype-acceptance-handoff/verify_evidence.py
python3 -m compileall -q prototype-acceptance-handoff/acceptance_harness.py prototype-acceptance-handoff/tests prototype-acceptance-handoff/run_evidence.py prototype-acceptance-handoff/verify_evidence.py
```

Evidence remains `NON_CATALOG`/`FAKE_BOUNDARY` and `BLOCKED` for release
purposes; the recorded revision, clean-worktree state, source hashes,
interpreter, and platform are provenance only, not live integration or release
evidence.
