"""Deterministic financial analysis pipeline for monthly financial-statement workbooks.

Python owns every number: loading, normalisation, subtotal handling, MoM/YoY/TTM
calculations, margins and data-quality validation. The Claude skill consumes the
validated, structured output and writes commentary; it never recalculates.
"""

__version__ = "0.1.0"
SCHEMA_VERSION = "1.0"
