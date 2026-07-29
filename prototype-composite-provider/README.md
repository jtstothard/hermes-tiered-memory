# PROTOTYPE — composite Hermes memory provider boundary

Throwaway, isolated prototype. **Do not deploy or copy this code into production.**

## Pre-registration

- **Question:** Can one composite Hermes memory provider wrap Mnemosyne L1 and Hindsight L2 while presenting one provider/tool namespace?
- **Hypothesis:** Yes. A Hermes-facing composite can load as the sole provider, read Mnemosyne first, fall back to Hindsight on a controlled miss, synchronously write L1, enqueue L2 `retain` asynchronously, suppress duplicate tool registration, and key all state by `(profile, bank)`.
- **Disconfirmation condition:** Any of the following is observed: provider loader rejects the composite; more than one memory tool namespace is registered; a read does not try L1 first; a controlled L1 miss fails to query L2; L1 write is not complete before the call returns; L2 failure blocks/undoes L1; or profile/bank data crosses boundaries.
- **Decision rule:** If all completion-test assertions and both disconfirming tests pass, the boundary is viable for a successor harden/publish effort. Otherwise the verdict is inconclusive/rejected and production adoption is not supported.
- **Scope/time-box:** One isolated stdlib Python prototype; fake Mnemosyne/Hindsight only; no live services, source checkout, Hermes config, or production files touched. Implementation + execution limited to this task.
- **Measurements:** test count/pass count; provider names/tools seen by loader; call order; L1 return-before-L2 evidence; queue/retain events; failure isolation; profile/bank key sets.

## Run

```bash
cd /home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-composite-provider
python3 -m unittest discover -s tests -v
```

The test runner writes `evidence.json` in this directory. It is generated evidence and may be deleted with the prototype.

## Design boundary

`HermesMemoryLoader` accepts exactly one `MemoryProvider` and registers the provider's tools once. `CompositeMemoryProvider` is the only Hermes-facing provider. The fakes are deliberately lower-level stores: they have no tool namespace, preventing duplicate registration by construction. The composite uses a per-key in-memory queue/worker to call Hindsight `retain` asynchronously after Mnemosyne's synchronous `write` returns.

This prototype models the required `retain`/`retain_batch_async` promotion direction; it intentionally does not use Hindsight reflect.
