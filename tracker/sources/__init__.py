"""Source adapters. Importing this package registers all built-in kinds."""
from . import (  # noqa: F401
    bibliocommons,
    chain_theaters,
    cloudlibrary,
    drafthouse,
    generic_page,
)
from .base import Source, build_sources  # noqa: F401
