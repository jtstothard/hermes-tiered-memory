Type: prototype
Status: resolved
Claimed by: default router session
Blocked by: none

## Question

Can a single composite Hermes memory plugin safely masquerade as the sole configured provider while wrapping Mnemosyne L1 and Hindsight L2, with one coherent tool namespace and no duplicate/conflicting memory tools?

**Completion test:** a minimal prototype loads through Hermes' provider interface, routes a read to Mnemosyne first and a controlled miss to Hindsight, writes synchronously to L1, queues an asynchronous L2 retain, and demonstrates profile/bank isolation with observable logs or tests. No production deployment.

## Answer

**Verdict: PASS within the tested boundary.** A single composite provider can present as `mnemosyne+hindsight`, expose one tool namespace (`memory_search`, `memory_store`), query L1 first, fall through to L2 on a controlled miss, synchronously write L1, queue asynchronous L2 retain, preserve `(profile, bank, key)` isolation, reject duplicate tool registration, and retain the L1 write when L2 times out.

**Evidence:** throwaway prototype at `/home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-composite-provider/`.

- `README.md` — hypothesis, disconfirmation conditions, decision rule, scope.
- `composite_provider.py` — prototype boundary.
- `tests/test_composite_provider.py` — seven executable tests.
- `evidence.json` — captured results.

**Independent verification:**

```text
python3 -m unittest discover -s tests -v
7 tests passed
python3 run_prototype.py
status: PASS
python3 -m compileall -q composite_provider.py tests
exit: 0
```

**Limitation:** this uses stdlib fakes, not live Hermes/Mnemosyne/Hindsight services. It proves the provider boundary and state semantics only; the next tickets must validate real adapter interfaces, metadata/promotion policy, failure persistence, and configuration.
