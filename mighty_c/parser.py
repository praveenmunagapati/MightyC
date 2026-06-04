"""
mighty_c/parser.py — Lark Transformer → Pydantic AST
=====================================================

This module contains:

1. ``MightyCTransformer`` — a Lark ``Transformer`` that converts the raw
   parse tree produced by the LALR(1) grammar into a validated Pydantic
   AST (defined in ``mighty_c.ast``).

2. ``parse_source(code)`` / ``parse_file(path)`` — convenience functions
   that run the full lex → parse → transform pipeline.

The transformer uses ``@v_args(meta=True)`` at class level so every
method receives a ``Meta`` object with line/column info, which we attach
to each AST node for later error reporting.

Design choices
--------------
* Binary expression rules (``bin_add``, ``bin_mul``, etc.) all funnel
  through ``_binop()`` to produce ``BinOp`` nodes — avoids duplication.
* The ``?``-prefixed grammar rules inline automatically, so the
  transformer only needs methods for the *named* alternatives.
* ``type_spec`` is reconstructed into a ``TypeAnnotation`` by inspecting
  the ``const_qualifier``, ``primitive_type``/``struct_type``, and the
  number of ``*`` tokens in the ``stars`` sub-rule.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from lark import Lark, Transformer, v_args, Token, Tree

from mighty_c.grammar import GRAMMAR
from mighty_c import ast as mc_ast


# ---------------------------------------------------------------------------
# Build the Lark parser once at module level (LALR is fast to construct).
# ---------------------------------------------------------------------------

_parser = Lark(
    GRAMMAR,
    parser="lalr",
    propagate_positions=True,   # populates Meta with line/col
    maybe_placeholders=False,
)


# ---------------------------------------------------------------------------
# Helper: extract line/col from a Lark Meta object
# ---------------------------------------------------------------------------

def _loc(meta) -> dict[str, int]:
    """Return ``{line: ..., col: ...}`` from a Lark ``Meta``, or empty dict."""
    d: dict[str, Any] = {}
    if hasattr(meta, "line"):
        d["line"] = meta.line
    if hasattr(meta, "column"):
        d["col"] = meta.column
    return d


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------

@v_args(meta=True)
class MightyCTransformer(Transformer):
    """Converts a Lark parse tree into a Mighty C Pydantic AST."""

    # ------------------------------------------------------------------ #
    # Top-level                                                          #
    # ------------------------------------------------------------------ #

    def start(self, meta, items: list) -> mc_ast.Program:
        return mc_ast.Program(declarations=items, **_loc(meta))

    def import_stmt(self, meta, items: list) -> mc_ast.ImportStmt:
        name = str(items[0])
        return mc_ast.ImportStmt(module_name=name, **_loc(meta))

    def export_decl(self, meta, items: list) -> mc_ast.Decl:
        decl = items[0]
        # Set is_export on the inner declaration
        if isinstance(decl, (mc_ast.FuncDecl, mc_ast.StructDecl, mc_ast.EnumDecl)):
            decl = decl.model_copy(update={"is_export": True})
        elif isinstance(decl, mc_ast.VarDecl):
            # VarDecl doesn't have is_export — wrap it? For now, pass through.
            pass
        return decl

    # ------------------------------------------------------------------ #
    # Declarations                                                       #
    # ------------------------------------------------------------------ #

    def func_decl(self, meta, items: list) -> mc_ast.FuncDecl:
        ret_type: mc_ast.TypeAnnotation = items[0]
        name = str(items[1])
        # items[2] is either a param_list (list[ParamDecl]) or the block
        if len(items) == 4:
            params = items[2]
            body = items[3]
        else:
            params = []
            body = items[2]
        return mc_ast.FuncDecl(
            return_type=ret_type,
            name=name,
            params=params,
            body=body,
            **_loc(meta),
        )

    def param_list(self, meta, items: list) -> list[mc_ast.ParamDecl]:
        return list(items)

    def param(self, meta, items: list) -> mc_ast.ParamDecl:
        return mc_ast.ParamDecl(type_ann=items[0], name=str(items[1]), **_loc(meta))

    def struct_decl(self, meta, items: list) -> mc_ast.StructDecl:
        name = str(items[0])
        fields = items[1:]
        return mc_ast.StructDecl(name=name, fields=fields, **_loc(meta))

    def struct_field(self, meta, items: list) -> mc_ast.StructField:
        return mc_ast.StructField(type_ann=items[0], name=str(items[1]), **_loc(meta))

    def enum_decl(self, meta, items: list) -> mc_ast.EnumDecl:
        name = str(items[0])
        members = items[1] if len(items) > 1 else []
        return mc_ast.EnumDecl(name=name, members=members, **_loc(meta))

    def enum_member_list(self, meta, items: list) -> list[mc_ast.EnumMember]:
        return list(items)

    def enum_member(self, meta, items: list) -> mc_ast.EnumMember:
        name = str(items[0])
        value = int(items[1]) if len(items) > 1 else None
        return mc_ast.EnumMember(name=name, value=value, **_loc(meta))

    # ------------------------------------------------------------------ #
    # Type specifications                                                #
    # ------------------------------------------------------------------ #

    def type_spec(self, meta, items: list) -> mc_ast.TypeAnnotation:
        is_const = False
        base = ""
        pointer_depth = 0

        for item in items:
            if isinstance(item, bool):
                # const_qualifier returns True
                is_const = item
            elif isinstance(item, str):
                base = item
            elif isinstance(item, int):
                pointer_depth = item
            elif isinstance(item, mc_ast.TypeAnnotation):
                # Shouldn't happen but handle gracefully
                return item

        return mc_ast.TypeAnnotation(
            base=base,
            pointer_depth=pointer_depth,
            is_const=is_const,
        )

    def const_qualifier(self, meta, items: list) -> bool:
        return True

    def primitive_type(self, meta, items: list) -> str:
        return str(items[0])

    def struct_type(self, meta, items: list) -> str:
        return f"struct {items[0]}"

    def stars(self, meta, items: list) -> int:
        return len(items)

    # ------------------------------------------------------------------ #
    # Statements                                                         #
    # ------------------------------------------------------------------ #

    def block(self, meta, items: list) -> mc_ast.Block:
        return mc_ast.Block(stmts=items, **_loc(meta))

    def var_decl(self, meta, items: list) -> mc_ast.VarDecl:
        type_ann = items[0]
        name = str(items[1])
        init = items[2] if len(items) > 2 else None
        return mc_ast.VarDecl(type_ann=type_ann, name=name, init=init, **_loc(meta))

    def return_stmt(self, meta, items: list) -> mc_ast.ReturnStmt:
        value = items[0] if items else None
        return mc_ast.ReturnStmt(value=value, **_loc(meta))

    def if_stmt(self, meta, items: list) -> mc_ast.IfStmt:
        condition = items[0]
        then_branch = items[1]
        else_branch = items[2] if len(items) > 2 else None
        return mc_ast.IfStmt(
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
            **_loc(meta),
        )

    def while_stmt(self, meta, items: list) -> mc_ast.WhileStmt:
        return mc_ast.WhileStmt(condition=items[0], body=items[1], **_loc(meta))

    def for_stmt(self, meta, items: list) -> mc_ast.ForStmt:
        # items: [init_or_None, cond_or_None, update_or_None, body]
        # Due to grammar optionality, we need to figure out which is which
        init = None
        condition = None
        update = None
        body = items[-1]  # body is always last

        remaining = items[:-1]
        if len(remaining) >= 1 and remaining[0] is not None:
            init = remaining[0]
        if len(remaining) >= 2 and remaining[1] is not None:
            condition = remaining[1]
        if len(remaining) >= 3 and remaining[2] is not None:
            update = remaining[2]

        return mc_ast.ForStmt(
            init=init,
            condition=condition,
            update=update,
            body=body,
            **_loc(meta),
        )

    def for_var_decl(self, meta, items: list) -> mc_ast.VarDecl:
        type_ann = items[0]
        name = str(items[1])
        init = items[2] if len(items) > 2 else None
        return mc_ast.VarDecl(type_ann=type_ann, name=name, init=init, **_loc(meta))

    def do_while_stmt(self, meta, items: list) -> mc_ast.DoWhileStmt:
        return mc_ast.DoWhileStmt(body=items[0], condition=items[1], **_loc(meta))

    def switch_stmt(self, meta, items: list) -> mc_ast.SwitchStmt:
        return mc_ast.SwitchStmt(expr=items[0], cases=items[1:], **_loc(meta))

    def case_clause(self, meta, items: list) -> mc_ast.CaseClause:
        return mc_ast.CaseClause(value=items[0], body=list(items[1:]), **_loc(meta))

    def default_clause(self, meta, items: list) -> mc_ast.CaseClause:
        return mc_ast.CaseClause(value=None, body=list(items), **_loc(meta))

    def break_stmt(self, meta, items: list) -> mc_ast.BreakStmt:
        return mc_ast.BreakStmt(**_loc(meta))

    def continue_stmt(self, meta, items: list) -> mc_ast.ContinueStmt:
        return mc_ast.ContinueStmt(**_loc(meta))

    def defer_stmt(self, meta, items: list) -> mc_ast.DeferStmt:
        return mc_ast.DeferStmt(stmt=items[0], **_loc(meta))

    def expr_stmt(self, meta, items: list) -> mc_ast.ExprStmt:
        return mc_ast.ExprStmt(expr=items[0], **_loc(meta))

    # ------------------------------------------------------------------ #
    # Expressions — binary operators                                     #
    # ------------------------------------------------------------------ #

    def _binop(self, meta, left, op, right) -> mc_ast.BinOp:
        return mc_ast.BinOp(op=str(op), left=left, right=right, **_loc(meta))

    def bin_or(self, meta, items):
        return self._binop(meta, items[0], "||", items[1])

    def bin_and(self, meta, items):
        return self._binop(meta, items[0], "&&", items[1])

    def bin_bitor(self, meta, items):
        return self._binop(meta, items[0], "|", items[1])

    def bin_bitxor(self, meta, items):
        return self._binop(meta, items[0], "^", items[1])

    def bin_bitand(self, meta, items):
        return self._binop(meta, items[0], "&", items[1])

    def bin_eq(self, meta, items):
        return self._binop(meta, items[0], str(items[1]), items[2])

    def bin_rel(self, meta, items):
        return self._binop(meta, items[0], str(items[1]), items[2])

    def bin_shift(self, meta, items):
        return self._binop(meta, items[0], str(items[1]), items[2])

    def bin_add(self, meta, items):
        return self._binop(meta, items[0], str(items[1]), items[2])

    def bin_mul(self, meta, items):
        return self._binop(meta, items[0], str(items[1]), items[2])

    # ------------------------------------------------------------------ #
    # Expressions — assignment                                           #
    # ------------------------------------------------------------------ #

    def assign_eq(self, meta, items):
        target = items[0]
        value = items[1]
        return mc_ast.BinOp(op="=", left=target, right=value, **_loc(meta))

    def assign_add(self, meta, items):
        target = items[0]
        value = items[1]
        expanded = mc_ast.BinOp(op="+", left=target, right=value, **_loc(meta))
        return mc_ast.BinOp(op="=", left=target, right=expanded, **_loc(meta))

    def assign_sub(self, meta, items):
        target = items[0]
        value = items[1]
        expanded = mc_ast.BinOp(op="-", left=target, right=value, **_loc(meta))
        return mc_ast.BinOp(op="=", left=target, right=expanded, **_loc(meta))

    def assign_mul(self, meta, items):
        target = items[0]
        value = items[1]
        expanded = mc_ast.BinOp(op="*", left=target, right=value, **_loc(meta))
        return mc_ast.BinOp(op="=", left=target, right=expanded, **_loc(meta))

    def assign_div(self, meta, items):
        target = items[0]
        value = items[1]
        expanded = mc_ast.BinOp(op="/", left=target, right=value, **_loc(meta))
        return mc_ast.BinOp(op="=", left=target, right=expanded, **_loc(meta))

    def assign_mod(self, meta, items):
        target = items[0]
        value = items[1]
        expanded = mc_ast.BinOp(op="%", left=target, right=value, **_loc(meta))
        return mc_ast.BinOp(op="=", left=target, right=expanded, **_loc(meta))

    # ------------------------------------------------------------------ #
    # Expressions — unary                                                #
    # ------------------------------------------------------------------ #

    def unary_neg(self, meta, items):
        return mc_ast.UnaryOp(op="-", operand=items[0], **_loc(meta))

    def unary_not(self, meta, items):
        return mc_ast.UnaryOp(op="!", operand=items[0], **_loc(meta))

    def unary_deref(self, meta, items):
        return mc_ast.UnaryOp(op="*", operand=items[0], **_loc(meta))

    def unary_addr(self, meta, items):
        return mc_ast.UnaryOp(op="&", operand=items[0], **_loc(meta))

    # ------------------------------------------------------------------ #
    # Expressions — postfix                                              #
    # ------------------------------------------------------------------ #

    def func_call(self, meta, items):
        callee = items[0]
        args = items[1] if len(items) > 1 else []
        return mc_ast.FuncCall(callee=callee, args=args, **_loc(meta))

    def arg_list(self, meta, items):
        return list(items)

    def array_sub(self, meta, items):
        return mc_ast.ArraySubscript(array=items[0], index=items[1], **_loc(meta))

    def member_dot(self, meta, items):
        return mc_ast.MemberAccess(
            object=items[0], member=str(items[1]), is_arrow=False, **_loc(meta)
        )

    def member_arrow(self, meta, items):
        return mc_ast.MemberAccess(
            object=items[0], member=str(items[1]), is_arrow=True, **_loc(meta)
        )

    # ------------------------------------------------------------------ #
    # Expressions — primary / literals                                   #
    # ------------------------------------------------------------------ #

    def int_lit(self, meta, items):
        text = str(items[0])
        if text.startswith("0x") or text.startswith("0X"):
            val = int(text, 16)
        elif text.startswith("0b") or text.startswith("0B"):
            val = int(text, 2)
        else:
            val = int(text)
        return mc_ast.IntLiteral(value=val, **_loc(meta))

    def float_lit(self, meta, items):
        return mc_ast.FloatLiteral(value=float(str(items[0])), **_loc(meta))

    def char_lit(self, meta, items):
        raw = str(items[0])
        # Strip surrounding quotes and unescape
        inner = raw[1:-1]
        value = _unescape_char(inner)
        return mc_ast.CharLiteral(value=value, **_loc(meta))

    def string_lit(self, meta, items):
        raw = str(items[0])
        # Strip surrounding quotes and unescape
        inner = raw[1:-1]
        value = _unescape_string(inner)
        return mc_ast.StringLiteral(value=value, **_loc(meta))

    def true_lit(self, meta, items):
        return mc_ast.BoolLiteral(value=True, **_loc(meta))

    def false_lit(self, meta, items):
        return mc_ast.BoolLiteral(value=False, **_loc(meta))

    def null_lit(self, meta, items):
        return mc_ast.NullLiteral(**_loc(meta))

    def ident(self, meta, items):
        return mc_ast.Identifier(name=str(items[0]), **_loc(meta))

    # ------------------------------------------------------------------ #
    # sizeof                                                             #
    # ------------------------------------------------------------------ #

    def sizeof_type(self, meta, items):
        return mc_ast.SizeOf(target_type=items[0], **_loc(meta))

    def sizeof_expr(self, meta, items):
        return mc_ast.SizeOf(target_expr=items[0], **_loc(meta))


# ---------------------------------------------------------------------------
# Escape-sequence helpers
# ---------------------------------------------------------------------------

_ESCAPE_MAP = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "0": "\0",
    "\\": "\\",
    "'": "'",
    '"': '"',
    "a": "\a",
    "b": "\b",
    "f": "\f",
    "v": "\v",
}


def _unescape_char(s: str) -> str:
    """Process a single (possibly escaped) character literal body."""
    if s.startswith("\\"):
        return _ESCAPE_MAP.get(s[1], s[1])
    return s


def _unescape_string(s: str) -> str:
    """Process escape sequences in a string literal body."""
    result = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            result.append(_ESCAPE_MAP.get(s[i + 1], s[i + 1]))
            i += 2
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_source(code: str) -> mc_ast.Program:
    """Parse Mighty C source code and return a validated Pydantic AST.

    Raises ``lark.exceptions.LarkError`` on syntax errors.
    """
    tree = _parser.parse(code)
    transformer = MightyCTransformer()
    return transformer.transform(tree)


def parse_file(path: str | Path) -> mc_ast.Program:
    """Read a ``.mc`` file and parse it into a Pydantic AST."""
    source = Path(path).read_text(encoding="utf-8")
    return parse_source(source)
