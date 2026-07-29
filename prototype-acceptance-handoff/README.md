# PROTOTYPE — 08 acceptance handoff

Throwaway local acceptance harness. **Do not deploy or copy into production.** It uses only Python stdlib fakes and a temporary/file-backed JSON outbox. It does not claim live Hermes, Mnemosyne, Hindsight, network, credential, or API compatibility.

## Pre-registered hypothesis and decision rule

- **Question:** Does the locked end-to-end contract from tickets 01–07 compose coherently enough for a harden + publish successor?
- **Hypothesis:** A composite provider can preserve cache-first behavior, durable asynchronous promotion, deterministic revisions, metadata provenance, isolation, and failure recovery without coupling foreground L1 operations to L2 availability.
- **Disconfirmation:** any contract case fails, duplicate tool namespace appears, L2 outage loses a foreground L1 write, replay duplicates durable identity, or profile/bank state crosses boundaries.
- **Decision rule:** PASS within the tested fake boundary only if all contract tests and the independent evidence check pass.

## Commands

```bash
cd /home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-acceptance-handoff
python3 -m unittest discover -s tests -v
python3 run_evidence.py
python3 verify_evidence.py
python3 -m compileall -q acceptance_harness.py tests run_evidence.py verify_evidence.py
```

`run_evidence.py` writes `evidence.json`; this is generated throwaway evidence.
