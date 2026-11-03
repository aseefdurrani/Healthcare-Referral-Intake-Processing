"""Declarative base. Tables are added in Phase 2."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared metadata for the intake schema."""
