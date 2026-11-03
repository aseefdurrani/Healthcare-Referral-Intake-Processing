"""Session factory. Phase 2 opens the pool. This shell does not connect."""

POOL_SIZE = 5
MAX_OVERFLOW = 0
POOL_PRE_PING = True


def build_engine() -> None:
    """Refuse to open PostgreSQL until the schema phase creates the engine."""

    raise RuntimeError("The database engine is not available in the Phase 1 shell.")
