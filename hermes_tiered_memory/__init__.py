"""Hermes Tiered Memory plugin entrypoint.

The composite registers as one external Hermes ``MemoryProvider``.  Its L1/L2
adapters are internal implementation details and never register separately.
"""

from hermes_tiered_memory.providers.composite import CompositeProvider

__all__ = ["CompositeProvider", "register", "register_memory_provider"]
__version__ = "1.0.0"


def register(ctx):
    """Register exactly one concrete provider or fail before registration."""
    try:
        from agent.memory_provider import MemoryProvider
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Hermes MemoryProvider API is unavailable; tiered provider was not registered"
        ) from exc

    if not issubclass(CompositeProvider, MemoryProvider) or CompositeProvider.__abstractmethods__:
        raise RuntimeError(
            "CompositeProvider does not implement the pinned Hermes MemoryProvider API; "
            "tiered provider was not registered"
        )

    register_provider = getattr(ctx, "register_memory_provider", None)
    if not callable(register_provider):
        raise RuntimeError(
            "Hermes registration context lacks register_memory_provider; "
            "tiered provider was not registered"
        )

    # Construct and validate before mutating the loader collector.  Runtime
    # configuration is intentionally checked in initialize(), when Hermes has
    # supplied the active home and profile scope.
    provider = CompositeProvider()
    if not provider.is_available():
        raise RuntimeError(
            "Tiered provider dependencies are unavailable; tiered provider was not registered"
        )
    register_provider(provider)


def register_memory_provider(ctx):
    """Backward-compatible alias for the plugin loader entrypoint."""
    return register(ctx)
