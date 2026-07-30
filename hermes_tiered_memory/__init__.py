"""Hermes Tiered Memory provider package.

The checked-in composite is not yet a usable Hermes ``MemoryProvider``.  Do
not let the plugin loader register it as though it were one: Hermes would
otherwise accept the object and fail later, after provider selection.
"""

from hermes_tiered_memory.providers.composite import CompositeProvider

__all__ = ["CompositeProvider"]
__version__ = "0.1.0"


def register_memory_provider(ctx):
    """Register the provider only when the pinned Hermes boundary is usable.

    This deliberately fails closed.  The repository's current
    ``CompositeProvider`` is an internal prototype with a non-Hermes
    constructor and does not implement the pinned ``MemoryProvider`` ABC.
    Registering it would produce a provider that Hermes cannot initialize.
    """
    try:
        from agent.memory_provider import MemoryProvider
    except (ImportError, ModuleNotFoundError) as exc:
        raise RuntimeError(
            "Hermes MemoryProvider API is unavailable; tiered provider was not registered"
        ) from exc

    provider_type = CompositeProvider
    if not issubclass(provider_type, MemoryProvider) or bool(provider_type.__abstractmethods__):
        raise RuntimeError(
            "CompositeProvider does not implement the pinned Hermes MemoryProvider API; "
            "tiered provider was not registered"
        )

    try:
        provider = provider_type()
    except Exception as exc:
        raise RuntimeError(
            "CompositeProvider cannot be constructed at the pinned Hermes adapter boundary; "
            "tiered provider was not registered"
        ) from exc

    register = getattr(ctx, "register_memory_provider", None)
    if not callable(register):
        raise RuntimeError(
            "Hermes registration context lacks register_memory_provider; "
            "tiered provider was not registered"
        )
    register(provider)