# PROTOTYPE — 08 acceptance handoff

Throwaway local acceptance harness. **Do not deploy or copy into production.** It uses only Python stdlib fakes and a temporary/file-backed JSON outbox. It does not claim live Hermes, Mnemosyne, Hindsight, network, credential, or API compatibility.

## Pre-registered hypothesis and decision rule

- **Question:** Does the locked end-to-end contract from tickets 01–07 compose coherently enough for a harden + publish successor?
- **Hypothesis:** A composite provider can preserve cache-first behavior, durable asynchronous promotion, deterministic revisions, metadata provenance, isolation, and failure recovery without coupling foreground L1 operations to L2 availability.
- **Disconfirmation:** any contract case fails, duplicate tool namespace appears, L2 outage loses a foreground L1 write, replay duplicates durable identity, or profile/bank state crosses boundaries.
- **Decision rule:** PASS within the tested fake boundary only if all contract tests and the independent evidence check pass.

## Commands

```bash
cd /home/hermes/hermes-tiered-memory
python3 -m unittest discover -s prototype-acceptance-handoff/tests -v
python3 prototype-acceptance-handoff/run_evidence.py
python3 prototype-acceptance-handoff/verify_evidence.py
python3 -m compileall -q prototype-acceptance-handoff/acceptance_harness.py prototype-acceptance-handoff/tests prototype-acceptance-handoff/run_evidence.py prototype-acceptance-handoff/verify_evidence.py
```

`run_evidence.py` writes `evidence.json`; this is generated throwaway evidence.
The commands are rooted at this repository and never use an unrelated scratch
checkout. Evidence remains `NON_CATALOG`/`FAKE_BOUNDARY` and `BLOCKED` for
release purposes; the recorded repository revision, worktree state, runner and
verifier hashes, interpreter, and platform are provenance only, not live
integration or release evidence.
