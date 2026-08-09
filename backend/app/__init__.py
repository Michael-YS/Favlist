"""Core package for the Favlist FastAPI application.

The package deliberately keeps persistence, authentication, validation, and
tagging rules separate so HTTP routes can compose them without duplicating
domain logic.
"""

from .config import Settings

__all__ = ["Settings"]
