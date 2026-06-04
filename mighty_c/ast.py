"""
mighty_c/ast.py — Pydantic AST Node Definitions
=================================================

Every node in the Mighty C abstract syntax tree is a strict, validated Pydantic model.
Nodes are organized into three families:

    Expr  — expressions that produce a value
    Stmt  — statements that perform an action
    Decl  — top-level declarations (functions, structs, enums, imports)

The root of every translation unit is a ``Program`` containing a list of declarations.

Design choices
--------------
* ``model_config = ConfigDict(frozen=True)`` makes AST nodes immutable once created,
  which is critical for safe semantic analysis and codegen passes.
* Optional ``line`` / ``col`` fields on the base class carry source location for
  error reporting without polluting every constructor.
* ``TypeAnnotation`` is *not* an AST node — it's a lightweight descriptor used inside
  declarations and type-checking, supporting pointer depth and const qualification.
"""

from __future__ import annotations

from typing import Optional, Union

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Type annotation (not an AST node per se, but used inside many nodes)
# ---------------------------------------------------------------------------

class TypeAnnotation(BaseModel):
    """Describes a Mighty C type, e.g. ``const int**``.

    Attributes:
        base:          One of the primitive types (``void``, ``bool``, ``int``,
                       ``float``, ``char``) or a user-defined struct name.
        pointer_depth: Number of ``*`` indirections. 0 = value type.
        is_const:      Whether the ``const`` qualifier is present.
    """
    model_config = ConfigDict(frozen=True)

    base: str
    pointer_depth: int = 0
    is_const: bool = False

    def __str__(self) -> str:
        prefix = "const " if self.is_const else ""
        stars = "*" * self.pointer_depth
        return f"{prefix}{self.base}{stars}"


# ---------------------------------------------------------------------------
# Base AST node
# ---------------------------------------------------------------------------

class ASTNode(BaseModel):
    """Common base for every AST node.

    Carries optional source-location fields so the semantic analyser and
    code generator can emit precise diagnostics.
    """
    model_config = ConfigDict(frozen=True)

    line: Optional[int] = None
    col: Optional[int] = None


# ===================================================================== #
#                          E X P R E S S I O N S                        #
# ===================================================================== #

class Expr(ASTNode):
    """Abstract base for all expression nodes."""
    pass


class IntLiteral(Expr):
    """Integer constant, e.g. ``42``."""
    value: int


class FloatLiteral(Expr):
    """Floating-point constant, e.g. ``3.14``."""
    value: float


class BoolLiteral(Expr):
    """Boolean literal — ``true`` or ``false``."""
    value: bool


class CharLiteral(Expr):
    """Character literal, e.g. ``'a'``."""
    value: str


class StringLiteral(Expr):
    """String literal, e.g. ``"hello"``.

    Represented as ``char*`` (``i8*``) in LLVM IR — a global constant
    array with a null terminator.
    """
    value: str


class NullLiteral(Expr):
    """The ``null`` keyword — a typed null pointer."""
    pass


class Identifier(Expr):
    """Reference to a variable, function, or enum member.

    ``name`` may be a simple name (``x``) or a scoped enum access
    stored as two identifiers connected by ``MemberAccess``.
    """
    name: str


class BinOp(Expr):
    """Binary operator expression, e.g. ``a + b``.

    ``op`` is the literal operator string: ``+``, ``-``, ``*``, ``/``,
    ``%``, ``==``, ``!=``, ``<``, ``>``, ``<=``, ``>=``, ``&&``, ``||``,
    ``&``, ``|``, ``^``, ``<<``, ``>>``.
    """
    op: str
    left: Expr
    right: Expr


class UnaryOp(Expr):
    """Unary operator expression.

    ``op`` is one of: ``-`` (negate), ``!`` (logical not), ``*`` (deref),
    ``&`` (address-of), ``++`` (pre-increment), ``--`` (pre-decrement).
    """
    op: str
    operand: Expr


class FuncCall(Expr):
    """Function call, e.g. ``add(1, 2)`` or ``printf("hello")``.

    ``callee`` is an ``Identifier`` for simple calls, or could be any
    expression for function-pointer calls (future extension).
    """
    callee: Expr
    args: list[Expr] = []


class MemberAccess(Expr):
    """Struct member access via ``.`` or ``->``.

    Also used for scoped enum access: ``Color.RED`` parses as
    ``MemberAccess(object=Identifier("Color"), member="RED", is_arrow=False)``.
    """
    object: Expr
    member: str
    is_arrow: bool = False


class ArraySubscript(Expr):
    """Array/pointer subscript, e.g. ``arr[i]``.

    Semantically equivalent to ``*(arr + i)`` — the codegen lowers it to
    a GEP instruction.
    """
    array: Expr
    index: Expr


class SizeOf(Expr):
    """``sizeof(type)`` or ``sizeof(expr)``.

    Evaluates to a compile-time integer constant.  Exactly one of
    ``target_type`` or ``target_expr`` will be set.
    """
    target_type: Optional[TypeAnnotation] = None
    target_expr: Optional[Expr] = None


class CastExpr(Expr):
    """Explicit type cast, e.g. ``(float)x``.

    Reserved for v2 — included in the AST so the grammar can support
    it without a major refactor later.
    """
    target_type: TypeAnnotation
    expr: Expr


# ===================================================================== #
#                          S T A T E M E N T S                          #
# ===================================================================== #

class Stmt(ASTNode):
    """Abstract base for all statement nodes."""
    pass


class Block(Stmt):
    """Brace-delimited block of statements ``{ ... }``.

    Introduces a new lexical scope for the symbol table.
    """
    stmts: list[Union[Stmt, "VarDecl"]] = []


class ReturnStmt(Stmt):
    """``return expr;`` or ``return;`` (void functions)."""
    value: Optional[Expr] = None


class IfStmt(Stmt):
    """``if (cond) { ... } else { ... }``."""
    condition: Expr
    then_branch: Stmt
    else_branch: Optional[Stmt] = None


class WhileStmt(Stmt):
    """``while (cond) { ... }``."""
    condition: Expr
    body: Stmt


class ForStmt(Stmt):
    """``for (init; cond; update) { ... }``.

    ``init`` can be a ``VarDecl`` or ``ExprStmt``.  All three clauses are
    optional (an infinite loop is ``for (;;) { ... }``).
    """
    init: Optional[Union[Stmt, "VarDecl"]] = None
    condition: Optional[Expr] = None
    update: Optional[Expr] = None
    body: Stmt


class DoWhileStmt(Stmt):
    """``do { ... } while (cond);``."""
    body: Stmt
    condition: Expr


class SwitchStmt(Stmt):
    """``switch (expr) { case ...: ... }``."""
    expr: Expr
    cases: list["CaseClause"] = []


class CaseClause(ASTNode):
    """A single ``case value:`` or ``default:`` arm inside a switch.

    ``value`` is ``None`` for the ``default`` arm.
    """
    value: Optional[Expr] = None
    body: list[Union[Stmt, "VarDecl"]] = []


class BreakStmt(Stmt):
    """``break;`` — exits the nearest enclosing loop or switch."""
    pass


class ContinueStmt(Stmt):
    """``continue;`` — jumps to the next iteration of the nearest enclosing loop."""
    pass


class DeferStmt(Stmt):
    """``defer stmt;`` — schedules *stmt* for execution when the enclosing
    block scope exits (LIFO order, like Go's ``defer``).

    The deferred statement is typically a function call (``defer free(p);``)
    but can be any single statement or a block.
    """
    stmt: Stmt


class ExprStmt(Stmt):
    """Expression used as a statement, e.g. ``foo();`` or ``x = 5;``."""
    expr: Expr


# ===================================================================== #
#                        D E C L A R A T I O N S                        #
# ===================================================================== #

class Decl(ASTNode):
    """Abstract base for all declaration nodes."""
    pass


class VarDecl(Decl, Stmt):
    """Variable declaration with optional initializer.

    ``int x = 42;`` → ``VarDecl(type_ann=int, name="x", init=IntLiteral(42))``

    Inherits from both ``Decl`` and ``Stmt`` because variable declarations
    can appear at the top level *and* inside function bodies.
    """
    type_ann: TypeAnnotation
    name: str
    init: Optional[Expr] = None


class ParamDecl(ASTNode):
    """A single function parameter: ``int x`` or ``const char* s``."""
    type_ann: TypeAnnotation
    name: str


class FuncDecl(Decl):
    """Function declaration / definition.

    ``export int add(int a, int b) { return a + b; }``

    If ``body`` is ``None``, this is a forward declaration / extern prototype.
    """
    return_type: TypeAnnotation
    name: str
    params: list[ParamDecl] = []
    body: Optional[Block] = None
    is_export: bool = False
    is_varargs: bool = False


class StructField(ASTNode):
    """A single field inside a struct definition."""
    type_ann: TypeAnnotation
    name: str


class StructDecl(Decl):
    """``struct Point { int x; int y; }``"""
    name: str
    fields: list[StructField] = []
    is_export: bool = False


class EnumMember(ASTNode):
    """A single enumerator inside an enum, e.g. ``RED`` or ``RED = 1``."""
    name: str
    value: Optional[int] = None


class EnumDecl(Decl):
    """``enum Color { RED, GREEN, BLUE }``"""
    name: str
    members: list[EnumMember] = []
    is_export: bool = False


class ImportStmt(Decl):
    """``import math;`` — imports exported symbols from another module."""
    module_name: str


# ===================================================================== #
#                               R O O T                                 #
# ===================================================================== #

class Program(ASTNode):
    """Root node of a Mighty C translation unit.

    Contains an ordered list of top-level declarations (functions, structs,
    enums, imports, and global variable declarations).
    """
    declarations: list[Decl] = []


# ---------------------------------------------------------------------------
# Pydantic v2 requires model_rebuild() for forward-reference resolution
# when models reference each other (e.g. Block contains VarDecl, BinOp
# contains Expr).  This call resolves all ``Union[...]`` and
# ``Optional[...]`` annotations that reference not-yet-defined classes.
# ---------------------------------------------------------------------------

# Rebuild all models that use forward references
Block.model_rebuild()
ForStmt.model_rebuild()
CaseClause.model_rebuild()
SwitchStmt.model_rebuild()
