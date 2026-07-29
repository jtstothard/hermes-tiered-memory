# Issue #3 research: Hermes plugin lifecycle and one-provider integration proof

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`
Hermes pin: `v0.19.0` (release `2026.7.20`), commit `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`.

## Scope and evidence policy

Source-only design report. Every claim below is grounded in a direct read of the pinned Hermes source tree checked out at the commit above. No Hermes process was started, no estate data was touched, no production path was read, and no commit or PR was opened. This resolves the lifecycle/integration question only; it does not execute the composite provider.

## Question (restated)

Can one packaged composite memory provider (Mnemosyne L1 + Hindsight L2) load, expose one tool namespace, survive startup/shutdown/reload, and avoid Hermes core changes on the pinned Hermes version?

## Decision

**Yes — the composite provider integrates cleanly as a single `MemoryProvider` plugin with no core changes.** The lifecycle, one-provider boundary, tool injection, and shutdown path are all exposed through the `MemoryProvider` ABC and the `MemoryManager`, and every one of them accepts exactly one provider. The composite registers as that one provider and owns both adapters internally; the adapters never register as Hermes providers.

This is a source-level proof. It does not verify that the composite *runs* end-to-end — that is the live-spike work of a later ticket. What it proves is that the integration surface is correct and sufficient: there is no hidden second-provider path, no lifecycle hook the composite must reach around the ABC to wire, and no core change required.

## The integration boundary, traced through source

### 1. Provider selection and loading (startup)

Entry point: `agent/agent_init.py`, inside agent initialization (L1449–1517).

```python
_mem_provider_name = mem_config.get("provider", "")  # reads memory.provider
agent._memory_manager = _MemoryManager()
_mp = _load_mem(_mem_provider_name)          # plugins.memory.load_memory_provider
if _mp and _mp.is_available():
    agent._memory_manager.add_provider(_mp)  # exactly one add
...
agent._memory_manager.initialize_all(**_init_kwargs)
```

The loader (`plugins/memory/__init__.py` `load_memory_provider`, L194) resolves the name to a directory (bundled `plugins/memory/<name>/` first, then `$HERMES_HOME/plugins/<name>/`), imports it, and returns one provider instance via the `register(ctx)` plugin-style path or a `MemoryProvider` subclass fallback (`_load_provider_from_dir`, L219–327). The bundled Hindsight provider uses the `register(ctx)` path (`plugins/memory/hindsight/__init__.py` L1966–1968: `ctx.register_memory_provider(HindsightMemoryProvider())`).

**Composite obligation:** ship as `$HERMES_HOME/plugins/<name>/__init__.py` exporting a `register(ctx)` that registers exactly one composite `MemoryProvider` instance. The directory must contain `register_memory_provider` or `MemoryProvider` text in `__init__.py` (the heuristic at `_is_memory_provider_dir`, L74–87) or it will not be discovered. The composite is selected by setting `memory.provider: <name>` in config.yaml.

### 2. The one-provider boundary (enforcement)

`MemoryManager.add_provider()` (`agent/memory_manager.py` L394–415):

```python
is_builtin = provider.name == "builtin"
if not is_builtin:
    if self._has_external:
        logger.warning("Rejected memory provider '%s' — external provider '%s' is "
            "already registered. Only one external memory provider is allowed...")
        return
    self._has_external = True
self._providers.append(provider)
```

A second external provider is **rejected with a warning and dropped** — not an exception, not a hard failure. The builtin provider (name `"builtin"`) is always allowed and always first.

**Composite obligation:** the composite must be the *only* external provider registered. Mnemosyne and Hindsight adapters must never each call `register_memory_provider`; only the composite does. The adapters are plain internal objects owned by the composite — Hermes never sees them. This is the critical correctness boundary for the tiering design: there is no supported way to register two providers, and the composite must not attempt it.

### 3. Tool namespace (exactly one)

`inject_memory_provider_tools(agent)` (`agent/memory_manager.py` L101–146) is called immediately after `initialize_all` (`agent_init.py` L1516–1517). It reads `memory_manager.get_all_tool_schemas()`, normalizes each schema, and appends each as `{"type": "function", "function": schema}` to `agent.tools`, skipping any name that collides with an existing tool or a reserved core tool name (`_HERMES_CORE_TOOLS`).

**Composite obligation:** the composite's `get_tool_schemas()` returns the *combined* tool set of both adapters under one namespace (e.g. `tiered_recall`, `tiered_retain`, or a unified memory tool surface). Tool names must not collide with core tools (`memory`, `hindsight_retain`, etc. are reserved). The composite's `handle_tool_call(tool_name, args)` dispatches internally to the right adapter. There is exactly one tool namespace because there is exactly one provider.

### 4. Lifecycle hooks (the full contract)

From the `MemoryProvider` ABC (`agent/memory_provider.py` L43–315). Abstract (must implement): `name`, `is_available()`, `initialize(session_id, **kwargs)`, `get_tool_schemas()`. Default-implemented (override as needed): `system_prompt_block()`, `prefetch()`, `queue_prefetch()`, `sync_turn()`, `handle_tool_call()`, `shutdown()`, and optional hooks `on_turn_start`, `on_session_end`, `on_session_switch`, `on_pre_compress`, `on_memory_write`, `on_delegation`, `backup_paths`.

`initialize()` kwargs (from `agent_init.py` L1463–1507 and the ABC docstring L62–83) always include `session_id`, `platform`, `hermes_home`, `agent_context`; may include `agent_identity` (profile name), `agent_workspace`, `user_id`, `user_id_alt`, `user_name`, `chat_id`, `chat_name`, `chat_type`, `thread_id`, `gateway_session_key`, `session_title`, `parent_session_id`. The composite uses `agent_identity`/`hermes_home` to scope the Mnemosyne L1 bank per profile, and `session_id`/`gateway_session_key` for session scope.

**`on_session_switch` is important for the composite** (ABC L176–218): it fires on `/resume`, `/branch`, `/reset`, `/new`, gateway equivalents, and context compression — any path that reassigns `session_id` without tearing the provider down. The composite must update or reset per-session L1 caches here so subsequent writes land in the correct session.

### 5. Shutdown path (session end / process exit)

`MemoryManager.shutdown_all()` (`agent/memory_manager.py` L1134–1151) drains the background sync/prefetch executor first (bounded by `_SYNC_DRAIN_TIMEOUT_S = 5.0s`), then calls `provider.shutdown()` on each provider in reverse order. It is reached via `AIAgent.shutdown_memory_provider(messages)` (`run_agent.py` L3452–3468):

```python
if self._memory_manager:
    self._memory_manager.on_session_end(messages or [])
    self._memory_manager.shutdown_all()
```

Callers of `shutdown_memory_provider()`:
- CLI atexit: `cli.py` L1206, L1233 (with session messages), L1239 (without, on error path).
- Gateway session expiry/reset: `gateway/run.py` L6508, L6521 (with messages), L6523 (without).
- One-shot runs: `hermes_cli/oneshot.py` L452 (with messages), L454 (without).
- Background review agents: `agent/background_review.py` L873, L939.

`on_session_end()` is called with the full message transcript *before* `shutdown_all()`. The drain timeout is hard: anything still wedged past 5s is abandoned and logged, with daemon threads dying with the interpreter.

**Composite obligation:** the composite's `on_session_end(messages)` is the place to enqueue any final Hindsight promotion via the outbox (the outbox must already be durable on disk to survive the 5s drain cutoff). The composite's `shutdown()` must close Mnemosyne's SQLite handle and flush/stop the Hindsight outbox worker. Because shutdown is bounded, **durable persistence must happen at write time (in `sync_turn`/`on_memory_write`), not deferred to shutdown** — shutdown can be abandoned. This resolves the open question from issue #2 ("does Hermes invoke a lifecycle method on shutdown, or must the composite register a close hook?"): `shutdown()` *is* invoked, but it is best-effort and bounded; the outbox is the durability mechanism, not `shutdown()`.

### 6. Reload (config change / `/reset`)

There is no hot-reload of memory providers within a live process. A provider is loaded once at agent init. `/reset` and `/new` call `on_session_switch` (re-scoping) and may call `shutdown_memory_provider` + re-init (full teardown and reload). Reloading the *provider code* requires a process restart.

**Composite obligation:** no special reload handling. The outbox must be re-readable on init so a restarted process drains pending promotions — `initialize()` should start the outbox drain.

## Evidence commands (reproducible)

```bash
# Pin verification
git -C /tmp/hermes-agent rev-parse HEAD
# 3ef6bbd201263d354fd83ec55b3c306ded2eb72a

# Lifecycle trace
grep -n "load_memory_provider\|add_provider\|initialize_all\|inject_memory_provider_tools" agent/agent_init.py
grep -n "_has_external\|Rejected memory provider\|def add_provider" agent/memory_manager.py
grep -n "def shutdown_all\|def initialize_all\|_SYNC_DRAIN_TIMEOUT_S" agent/memory_manager.py
grep -n "def shutdown_memory_provider\|on_session_end\|shutdown_all" run_agent.py
grep -rn "shutdown_memory_provider" cli.py gateway/run.py hermes_cli/oneshot.py
```

## What the composite must implement (checklist)

- [ ] `$HERMES_HOME/plugins/<name>/__init__.py` with `register(ctx)` calling `ctx.register_memory_provider(CompositeProvider())`.
- [ ] `__init__.py` source contains `register_memory_provider` or `MemoryProvider` (discovery heuristic).
- [ ] `class CompositeProvider(MemoryProvider)` with abstract methods: `name`, `is_available`, `initialize`, `get_tool_schemas`.
- [ ] `initialize()` scopes Mnemosyne bank by `agent_identity`/`hermes_home`; starts Hindsight outbox drain.
- [ ] `get_tool_schemas()` returns one unified, non-colliding tool namespace.
- [ ] `handle_tool_call()` dispatches to the correct internal adapter.
- [ ] `on_session_switch()` resets per-session L1 caches.
- [ ] `on_memory_write()` mirrors builtin memory writes to L1 + outbox (durability at write time).
- [ ] `shutdown()` closes L1, stops outbox worker (best-effort, 5s bounded).
- [ ] `backup_paths()` declares the outbox + L1 paths if outside `HERMES_HOME`.
- [ ] Adapters (Mnemosyne, Hindsight) are internal objects; they never call `register_memory_provider`.

## Unresolved / newly visible successor questions

1. **Live run proof.** This is source-level only. A later ticket must instantiate the composite in a disposable `HERMES_HOME`, start a disposable Hindsight, and prove load → initialize → tool inject → write → shutdown → reload actually works. (This is the vertical-slice ticket the other successor tickets assume.)
2. **`on_memory_write` vs `sync_turn` routing.** Which path carries Mnemosyne writes that should promote to Hindsight? `on_memory_write` fires on builtin-tool writes; `sync_turn` fires after every turn. The composite's promotion trigger needs a defined policy — likely candidate for the promotion-policy ticket.
3. **Outbox durability vs the 5s shutdown drain.** Confirmed shutdown is bounded and best-effort, so the outbox is mandatory. The durable-outbox ticket must define the on-disk format, idempotency key, and drain-on-init semantics.
4. **Tool namespace shape.** Whether the composite exposes one merged tool, mirrors Hindsight's (`retain`/`recall`/`reflect`), or invents tier-aware tools is a UX/policy decision for a later ticket — the lifecycle imposes no constraint beyond name-uniqueness.

## Concise recommendation for issue #3

Proceed: the composite provider is a single `MemoryProvider` plugin registered via `register(ctx)`, selected by `memory.provider`, loaded once at agent init, bounded to one external provider by `MemoryManager.add_provider()`, given one tool namespace by `inject_memory_provider_tools()`, and torn down via `shutdown_memory_provider()` → `on_session_end()` → `shutdown_all()` on CLI exit, gateway session expiry, and one-shot completion. No core change is required. Durability belongs in the outbox, not in `shutdown()`. The next ticket should prove this with a live run in a disposable environment.
