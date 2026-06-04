"""
Mighty C Compiler
=================

A modern C-dialect compiler that strips away legacy baggage (preprocessor, goto, unions)
and replaces them with modular imports, defer-based cleanup, and scoped enums.

Pipeline: Source (.mc) → Lark Parse → Pydantic AST → Semantic Analysis → LLVM IR → Native Binary
"""

__version__ = "0.1.0"
