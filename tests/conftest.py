"""Test setup — stubs heavy optional deps so the sizer / picker /
compute_mode tests can run without installing the full Streamlit
+ anthropic + pandas stack.

The functions under test are pure (regex + lookup tables) and don't
need any of these at runtime; but the package __init__ chains
sometimes pull them in eagerly. Stubs are no-ops.
"""
from __future__ import annotations

import sys
import types


def _stub(name: str, **attrs):
    if name in sys.modules:
        return
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod


# Stub heavy optional deps — only loaded when src/pillars/__init__ is
# imported via the pillars dispatcher path. Pure-function tests don't
# need them.
_stub("anthropic",
      APIStatusError=Exception,
      APIConnectionError=Exception,
      RateLimitError=Exception,
      BadRequestError=Exception,
      OverloadedError=Exception,
      Anthropic=lambda **kw: None)

try:
    import pydantic  # noqa: F401
except ImportError:
    _stub("pydantic",
          BaseModel=type("BaseModel", (), {}),
          Field=lambda **kw: None)

try:
    import streamlit  # noqa: F401
except ImportError:
    _stub("streamlit", session_state={})

try:
    import pandas  # noqa: F401
except ImportError:
    # Minimal stub — only used to make src/parsers/inventory.py importable.
    pandas_stub = types.ModuleType("pandas")
    pandas_stub.DataFrame = type("DataFrame", (), {})
    pandas_stub.read_csv = lambda *a, **kw: None
    pandas_stub.read_excel = lambda *a, **kw: None
    pandas_stub.NaT = None
    pandas_stub.isna = lambda x: x is None
    pandas_stub.notna = lambda x: x is not None
    pandas_stub.NA = None
    sys.modules["pandas"] = pandas_stub

try:
    import openpyxl  # noqa: F401
except ImportError:
    _stub("openpyxl")
    _stub("openpyxl.styles", Font=type, Alignment=type, PatternFill=type,
          Border=type, Side=type)
    _stub("xlsxwriter")
    _stub("xlsxwriter.workbook")
