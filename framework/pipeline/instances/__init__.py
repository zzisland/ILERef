"""Unified runtime instances and explicit historical import boundary."""
from .model import UnifiedRefactoringInstance
from .legacy_adapter import LegacyInstanceAdapter

__all__ = ['UnifiedRefactoringInstance', 'LegacyInstanceAdapter']
