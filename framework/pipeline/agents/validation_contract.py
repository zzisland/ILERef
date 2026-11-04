"""Compatibility import alias; implementation lives in verification.

Alias the module object so existing monkeypatch/injection paths retain effect.
"""
import importlib as _importlib
import sys as _sys
_VERIFICATION_MODULE = 'pipeline.verification.engineering.contract'
_sys.modules[__name__] = _importlib.import_module(_VERIFICATION_MODULE)
