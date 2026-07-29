Type: research
Status: resolved
Blocked by: 03

## Question

Which Mnemosyne metadata can be mapped losslessly into Hindsight retain/batch-retain and back through the existing Hindsight importer, and which fields require an extension or explicit loss policy?

**Completion test:** a field-by-field mapping table covers source IDs, bank/profile, fact type, entities, tags, timestamps, consolidation provenance, confidence, and deletion/update markers, with a round-trip fixture or documented exception for every field.

## Answer

**Decision:** use a dedicated Hindsight `metadata` provenance envelope for Mnemosyne-only fields. Treat Hindsight-generated fact IDs, extracted fact boundaries, canonical entities, proof counts, and consolidation timestamps as derived rather than round-trippable.

| Mnemosyne field | Hindsight mapping | Round-trip |
|---|---|---|
| `content` | retain content / batch `contents[].content` | Source text lossless; fact boundaries may split |
| Source memory ID | `document_id` plus duplicated provenance metadata | Partial; Hindsight fact IDs are generated |
| Profile/bank | `bank_id` | Lossless at routing level |
| `source` | Explicit metadata | Lossless only when copied |
| Fact type | `fact_type_override` where supported, original in metadata | Lossy natively |
| Entities | `contents[].entities` | Input representable; canonicalization is lossy |
| Tags | `contents[].tags` / `document_tags` | Supplied tags lossless |
| Event timestamp | `event_date` | Input preserved; derived temporal fields may differ |
| Consolidation markers | Metadata envelope | Lost by default |
| Confidence/importance | Metadata envelope | Not a native Hindsight confidence field |
| Deletion/invalidation/supersession | Metadata plus separate update/delete policy | Lost by default; `update_mode=replace/append` is insufficient alone |

The existing Hindsight→Mnemosyne importer preserves substantial metadata, but reconstructs IDs, confidence-like importance, sessions, and deletion state by policy rather than native round-trip.

**Evidence:** `/tmp/mnemosyne-verify/mnemosyne/core/importers/hindsight.py`; existing importer tests; Hindsight `memory_engine.py` and retain type definitions.

**Verification:**

```text
PYTHONPATH=/tmp/mnemosyne-verify python3 -m pytest -q /tmp/mnemosyne-verify/tests/test_importers/test_hindsight.py
5 passed in 3.20s
```
