"""
Hermes Tiered Memory — Production Composite Provider

A composite Hermes memory provider combining Mnemosyne L1 and Hindsight L2.
"""

from hermes_tiered_memory.providers.composite import CompositeProvider

__all__ = ["CompositeProvider"]
__version__ = "0.1.0"


def register_memory_provider(ctx):
    """Register the composite memory provider with Hermes."""
    from hermes_tiered_memory.providers.composite import CompositeProvider
    ctx.register_memory_provider(CompositeProvider)