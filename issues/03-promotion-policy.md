Type: grilling
Status: resolved
Claimed by: default router session
Blocked by: 01

## Question

What exact memories are promoted from Mnemosyne L1 to Hindsight L2, and what trigger is authoritative: post-consolidation event, periodic batch, age/confidence threshold, explicit importance, or a hybrid?

**Completion test:** a deterministic, configurable promotion predicate and trigger policy is documented with examples for episodic summaries, extracted facts, low-confidence/working memories, updates, and deletes; it does not depend on Hindsight reflect.

## Answer

**Decision:** use hybrid triggering and a permissive eligibility predicate.

### Trigger

1. Promote after a successful Mnemosyne consolidation event.
2. Run a periodic reconciliation sweep to recover missed events and retry pending items.
3. Promotion writes use Hindsight retain/batch-retain only; they do not depend on reflect.

### Eligibility

Promote all successful consolidated episodic summaries and extracted facts by default. Keep raw working memory L1-only unless explicitly marked important. Do not promote malformed output, consolidation failures, or explicitly invalidated/superseded memories.

Do not impose a hard confidence/importance threshold initially. Preserve confidence, veracity, and importance in the metadata provenance envelope for later calibration. This avoids silent data loss before estate-specific quality data exists.

Updates and deletes are promoted as idempotent revisions/tombstones under the identity policy. Reconciliation must be able to discover and retry every eligible item.

**Limitation:** the exact stable identity and conflict behavior is defined by ticket 04; queue durability and observability are defined by ticket 06.
