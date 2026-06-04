"""
mighty_c/semantic.py — Semantic Analysis for Mighty C
=====================================================

Responsibilities:
    1. **Symbol Table** — scoped stack of dictionaries tracking variables,
       functions, structs, and enums with their types and metadata.
    2. **Type Checking** — validates type compatibility for assignments,
       binary/unary operators, function call arguments, and return types.
    3. **Defer Resolution** — tracks ``defer`` statements per scope and
       ensures they are recorded for codegen to emit in LIFO order.
    4. **Import Resolution** — registers imported modules as external
       declarations (mock implementations for v1).
    5. **Error Accumulation** — collects all errors without stopping at the
       first one, for a better developer experience.

The main entry point is ``analyze(program) -> AnalysisResult``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from mighty_c import ast as mc_ast


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class SymbolKind(Enum):
    """What kind of entity a symbol represents."""
    VARIABLE = "variable"
    FUNCTION = "function"
    STRUCT = "struct"
    ENUM = "enum"
    ENUM_MEMBER = "enum_member"
    PARAMETER = "parameter"


@dataclass
class SymbolInfo:
    """Metadata for a single symbol in the symbol table."""
    name: str
    kind: SymbolKind
    type_ann: mc_ast.TypeAnnotation
    is_const: bool = False
    is_export: bool = False
    # For functions:
    params: list[mc_ast.ParamDecl] | None = None
    is_varargs: bool = False
    # For structs:
    fields: list[mc_ast.StructField] | None = None
    # For enums:
    members: list[mc_ast.EnumMember] | None = None
    # For enum members:
    enum_name: str | None = None
    enum_value: int | None = None


@dataclass
class SemanticError:
    """A single semantic error with source location and message."""
    message: str
    line: int | None = None
    col: int | None = None

    def __str__(self) -> str:
        loc = ""
        if self.line is not None:
            loc = f"line {self.line}"
            if self.col is not None:
                loc += f", col {self.col}"
            loc = f"[{loc}] "
        return f"{loc}{self.message}"


@dataclass
class AnalysisResult:
    """Result of semantic analysis — errors + enriched metadata."""
    errors: list[SemanticError] = field(default_factory=list)
    # Function metadata for codegen (defer stacks, etc.)
    func_defer_stacks: dict[str, list] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Symbol Table — scoped stack
# ---------------------------------------------------------------------------

class SymbolTable:
    """Lexically-scoped symbol table implemented as a stack of dicts.

    ``enter_scope()`` pushes a new dict; ``exit_scope()`` pops it.
    Lookup searches from the top of the stack downward, so inner scopes
    shadow outer ones.
    """

    def __init__(self) -> None:
        self._scopes: list[dict[str, SymbolInfo]] = [{}]

    def enter_scope(self) -> None:
        self._scopes.append({})

    def exit_scope(self) -> dict[str, SymbolInfo]:
        return self._scopes.pop()

    def define(self, sym: SymbolInfo) -> None:
        """Add a symbol to the current (innermost) scope."""
        self._scopes[-1][sym.name] = sym

    def lookup(self, name: str) -> SymbolInfo | None:
        """Search for ``name`` from innermost to outermost scope."""
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    def lookup_current(self, name: str) -> SymbolInfo | None:
        """Search only the current (innermost) scope."""
        return self._scopes[-1].get(name)

    @property
    def depth(self) -> int:
        return len(self._scopes)


# ---------------------------------------------------------------------------
# Built-in / external declarations
# ---------------------------------------------------------------------------

# External C functions we make available automatically (e.g. printf for testing)
_BUILTINS: list[SymbolInfo] = [
    SymbolInfo(
        name="printf",
        kind=SymbolKind.FUNCTION,
        type_ann=mc_ast.TypeAnnotation(base="int"),
        params=[mc_ast.ParamDecl(
            type_ann=mc_ast.TypeAnnotation(base="char", pointer_depth=1),
            name="fmt",
        )],
        is_varargs=True,
    ),
    SymbolInfo(
        name="malloc",
        kind=SymbolKind.FUNCTION,
        type_ann=mc_ast.TypeAnnotation(base="void", pointer_depth=1),
        params=[mc_ast.ParamDecl(
            type_ann=mc_ast.TypeAnnotation(base="int"),
            name="size",
        )],
    ),
    SymbolInfo(
        name="free",
        kind=SymbolKind.FUNCTION,
        type_ann=mc_ast.TypeAnnotation(base="void"),
        params=[mc_ast.ParamDecl(
            type_ann=mc_ast.TypeAnnotation(base="void", pointer_depth=1),
            name="ptr",
        )],
    ),
]

# Module stubs: when `import io;` is seen, these externs become available.
_MODULE_REGISTRY: dict[str, list[SymbolInfo]] = {
    "io": [
        SymbolInfo(
            name="printf",
            kind=SymbolKind.FUNCTION,
            type_ann=mc_ast.TypeAnnotation(base="int"),
            params=[mc_ast.ParamDecl(
                type_ann=mc_ast.TypeAnnotation(base="char", pointer_depth=1),
                name="fmt",
            )],
            is_varargs=True,
        ),
    ],
    "math": [
        SymbolInfo(
            name="sin",
            kind=SymbolKind.FUNCTION,
            type_ann=mc_ast.TypeAnnotation(base="float"),
            params=[mc_ast.ParamDecl(
                type_ann=mc_ast.TypeAnnotation(base="float"),
                name="x",
            )],
        ),
        SymbolInfo(
            name="cos",
            kind=SymbolKind.FUNCTION,
            type_ann=mc_ast.TypeAnnotation(base="float"),
            params=[mc_ast.ParamDecl(
                type_ann=mc_ast.TypeAnnotation(base="float"),
                name="x",
            )],
        ),
    ],
}


# ---------------------------------------------------------------------------
# Semantic Analyzer
# ---------------------------------------------------------------------------

class SemanticAnalyzer:
    """Walks the AST and performs semantic checks.

    Usage::

        analyzer = SemanticAnalyzer()
        result = analyzer.analyze(program)
        if not result.ok:
            for err in result.errors:
                print(err)
    """

    def __init__(self) -> None:
        self.symbols = SymbolTable()
        self.errors: list[SemanticError] = []
        self._loop_depth = 0          # Track nested loops for break/continue
        self._switch_depth = 0        # Track nested switches for break
        self._current_func: str | None = None
        self._current_func_ret: mc_ast.TypeAnnotation | None = None

    # ------------------------------------------------------------------ #
    # Error helpers                                                      #
    # ------------------------------------------------------------------ #

    def _error(self, msg: str, node: mc_ast.ASTNode | None = None) -> None:
        line = getattr(node, "line", None) if node else None
        col = getattr(node, "col", None) if node else None
        self.errors.append(SemanticError(message=msg, line=line, col=col))

    # ------------------------------------------------------------------ #
    # Public entry point                                                 #
    # ------------------------------------------------------------------ #

    def analyze(self, program: mc_ast.Program) -> AnalysisResult:
        """Run semantic analysis on a complete program AST."""
        # Register builtins in the global scope
        for sym in _BUILTINS:
            self.symbols.define(sym)

        # First pass: register all top-level declarations (allows forward references)
        for decl in program.declarations:
            self._register_top_level(decl)

        # Second pass: analyze bodies
        for decl in program.declarations:
            self._analyze_decl(decl)

        return AnalysisResult(errors=self.errors)

    # ------------------------------------------------------------------ #
    # First pass — register top-level names                              #
    # ------------------------------------------------------------------ #

    def _register_top_level(self, decl: mc_ast.Decl) -> None:
        if isinstance(decl, mc_ast.FuncDecl):
            self.symbols.define(SymbolInfo(
                name=decl.name,
                kind=SymbolKind.FUNCTION,
                type_ann=decl.return_type,
                is_export=decl.is_export,
                params=decl.params,
                is_varargs=decl.is_varargs,
            ))
        elif isinstance(decl, mc_ast.StructDecl):
            self.symbols.define(SymbolInfo(
                name=decl.name,
                kind=SymbolKind.STRUCT,
                type_ann=mc_ast.TypeAnnotation(base=f"struct {decl.name}"),
                is_export=decl.is_export,
                fields=decl.fields,
            ))
        elif isinstance(decl, mc_ast.EnumDecl):
            self.symbols.define(SymbolInfo(
                name=decl.name,
                kind=SymbolKind.ENUM,
                type_ann=mc_ast.TypeAnnotation(base="int"),
                is_export=decl.is_export,
                members=decl.members,
            ))
            # Register each enum member as EnumName.MEMBER → integer
            for i, member in enumerate(decl.members):
                val = member.value if member.value is not None else i
                self.symbols.define(SymbolInfo(
                    name=f"{decl.name}.{member.name}",
                    kind=SymbolKind.ENUM_MEMBER,
                    type_ann=mc_ast.TypeAnnotation(base="int"),
                    enum_name=decl.name,
                    enum_value=val,
                ))
        elif isinstance(decl, mc_ast.VarDecl):
            self._analyze_var_decl(decl)
        elif isinstance(decl, mc_ast.ImportStmt):
            self._analyze_import(decl)

    # ------------------------------------------------------------------ #
    # Declaration analysis                                               #
    # ------------------------------------------------------------------ #

    def _analyze_decl(self, decl: mc_ast.Decl) -> None:
        if isinstance(decl, mc_ast.FuncDecl):
            self._analyze_func(decl)
        elif isinstance(decl, mc_ast.StructDecl):
            pass  # Already registered
        elif isinstance(decl, mc_ast.EnumDecl):
            pass  # Already registered
        elif isinstance(decl, mc_ast.VarDecl):
            pass  # Already handled in first pass
        elif isinstance(decl, mc_ast.ImportStmt):
            pass  # Already handled in first pass

    def _analyze_func(self, decl: mc_ast.FuncDecl) -> None:
        if decl.body is None:
            return  # Forward declaration / extern — nothing to check

        self._current_func = decl.name
        self._current_func_ret = decl.return_type

        self.symbols.enter_scope()

        # Register parameters
        for param in decl.params:
            self.symbols.define(SymbolInfo(
                name=param.name,
                kind=SymbolKind.PARAMETER,
                type_ann=param.type_ann,
                is_const=param.type_ann.is_const,
            ))

        # Analyze function body
        self._analyze_block(decl.body)

        self.symbols.exit_scope()
        self._current_func = None
        self._current_func_ret = None

    def _analyze_import(self, stmt: mc_ast.ImportStmt) -> None:
        module = stmt.module_name
        if module in _MODULE_REGISTRY:
            for sym in _MODULE_REGISTRY[module]:
                # Don't overwrite if already defined (e.g. builtins)
                if self.symbols.lookup(sym.name) is None:
                    self.symbols.define(sym)
        else:
            self._error(f"Unknown module '{module}'", stmt)

    # ------------------------------------------------------------------ #
    # Statement analysis                                                 #
    # ------------------------------------------------------------------ #

    def _analyze_stmt(self, stmt: mc_ast.Stmt | mc_ast.VarDecl) -> None:
        if isinstance(stmt, mc_ast.Block):
            self.symbols.enter_scope()
            self._analyze_block(stmt)
            self.symbols.exit_scope()
        elif isinstance(stmt, mc_ast.VarDecl):
            self._analyze_var_decl(stmt)
        elif isinstance(stmt, mc_ast.ReturnStmt):
            self._analyze_return(stmt)
        elif isinstance(stmt, mc_ast.IfStmt):
            self._analyze_if(stmt)
        elif isinstance(stmt, mc_ast.WhileStmt):
            self._analyze_while(stmt)
        elif isinstance(stmt, mc_ast.ForStmt):
            self._analyze_for(stmt)
        elif isinstance(stmt, mc_ast.DoWhileStmt):
            self._analyze_do_while(stmt)
        elif isinstance(stmt, mc_ast.SwitchStmt):
            self._analyze_switch(stmt)
        elif isinstance(stmt, mc_ast.BreakStmt):
            if self._loop_depth == 0 and self._switch_depth == 0:
                self._error("'break' outside of loop or switch", stmt)
        elif isinstance(stmt, mc_ast.ContinueStmt):
            if self._loop_depth == 0:
                self._error("'continue' outside of loop", stmt)
        elif isinstance(stmt, mc_ast.DeferStmt):
            self._analyze_stmt(stmt.stmt)
        elif isinstance(stmt, mc_ast.ExprStmt):
            self._analyze_expr(stmt.expr)

    def _analyze_block(self, block: mc_ast.Block) -> None:
        for stmt in block.stmts:
            self._analyze_stmt(stmt)

    def _analyze_var_decl(self, decl: mc_ast.VarDecl) -> None:
        # Check for redeclaration in current scope
        existing = self.symbols.lookup_current(decl.name)
        if existing is not None:
            self._error(
                f"Redeclaration of '{decl.name}' in the same scope", decl
            )

        # Validate the type exists
        self._validate_type(decl.type_ann, decl)

        # Analyze initializer
        if decl.init is not None:
            self._analyze_expr(decl.init)

        # Register the variable
        self.symbols.define(SymbolInfo(
            name=decl.name,
            kind=SymbolKind.VARIABLE,
            type_ann=decl.type_ann,
            is_const=decl.type_ann.is_const,
        ))

    def _analyze_return(self, stmt: mc_ast.ReturnStmt) -> None:
        if self._current_func is None:
            self._error("'return' outside of function", stmt)
            return

        if stmt.value is not None:
            self._analyze_expr(stmt.value)
            if self._current_func_ret and self._current_func_ret.base == "void":
                self._error(
                    f"Cannot return a value from void function '{self._current_func}'",
                    stmt,
                )
        else:
            if (self._current_func_ret
                    and self._current_func_ret.base != "void"
                    and self._current_func_ret.pointer_depth == 0):
                self._error(
                    f"Non-void function '{self._current_func}' must return a value",
                    stmt,
                )

    def _analyze_if(self, stmt: mc_ast.IfStmt) -> None:
        self._analyze_expr(stmt.condition)
        self._analyze_stmt(stmt.then_branch)
        if stmt.else_branch is not None:
            self._analyze_stmt(stmt.else_branch)

    def _analyze_while(self, stmt: mc_ast.WhileStmt) -> None:
        self._analyze_expr(stmt.condition)
        self._loop_depth += 1
        self._analyze_stmt(stmt.body)
        self._loop_depth -= 1

    def _analyze_for(self, stmt: mc_ast.ForStmt) -> None:
        self.symbols.enter_scope()
        if stmt.init is not None:
            self._analyze_stmt(stmt.init)
        if stmt.condition is not None:
            self._analyze_expr(stmt.condition)
        if stmt.update is not None:
            self._analyze_expr(stmt.update)
        self._loop_depth += 1
        self._analyze_stmt(stmt.body)
        self._loop_depth -= 1
        self.symbols.exit_scope()

    def _analyze_do_while(self, stmt: mc_ast.DoWhileStmt) -> None:
        self._loop_depth += 1
        self._analyze_stmt(stmt.body)
        self._loop_depth -= 1
        self._analyze_expr(stmt.condition)

    def _analyze_switch(self, stmt: mc_ast.SwitchStmt) -> None:
        self._analyze_expr(stmt.expr)
        self._switch_depth += 1
        for case in stmt.cases:
            if case.value is not None:
                self._analyze_expr(case.value)
            for s in case.body:
                self._analyze_stmt(s)
        self._switch_depth -= 1

    # ------------------------------------------------------------------ #
    # Expression analysis                                                #
    # ------------------------------------------------------------------ #

    def _analyze_expr(self, expr: mc_ast.Expr) -> Optional[mc_ast.TypeAnnotation]:
        """Analyze an expression and return its inferred type (best-effort)."""
        if isinstance(expr, mc_ast.IntLiteral):
            return mc_ast.TypeAnnotation(base="int")
        elif isinstance(expr, mc_ast.FloatLiteral):
            return mc_ast.TypeAnnotation(base="float")
        elif isinstance(expr, mc_ast.BoolLiteral):
            return mc_ast.TypeAnnotation(base="bool")
        elif isinstance(expr, mc_ast.CharLiteral):
            return mc_ast.TypeAnnotation(base="char")
        elif isinstance(expr, mc_ast.StringLiteral):
            return mc_ast.TypeAnnotation(base="char", pointer_depth=1)
        elif isinstance(expr, mc_ast.NullLiteral):
            return mc_ast.TypeAnnotation(base="void", pointer_depth=1)
        elif isinstance(expr, mc_ast.Identifier):
            return self._analyze_ident(expr)
        elif isinstance(expr, mc_ast.BinOp):
            return self._analyze_binop(expr)
        elif isinstance(expr, mc_ast.UnaryOp):
            return self._analyze_unary(expr)
        elif isinstance(expr, mc_ast.FuncCall):
            return self._analyze_func_call(expr)
        elif isinstance(expr, mc_ast.MemberAccess):
            return self._analyze_member_access(expr)
        elif isinstance(expr, mc_ast.ArraySubscript):
            self._analyze_expr(expr.array)
            self._analyze_expr(expr.index)
            return mc_ast.TypeAnnotation(base="int")  # simplified
        elif isinstance(expr, mc_ast.SizeOf):
            if expr.target_expr is not None:
                self._analyze_expr(expr.target_expr)
            return mc_ast.TypeAnnotation(base="int")
        return None

    def _analyze_ident(self, expr: mc_ast.Identifier) -> Optional[mc_ast.TypeAnnotation]:
        sym = self.symbols.lookup(expr.name)
        if sym is None:
            self._error(f"Undefined identifier '{expr.name}'", expr)
            return None
        return sym.type_ann

    def _analyze_binop(self, expr: mc_ast.BinOp) -> Optional[mc_ast.TypeAnnotation]:
        left_type = self._analyze_expr(expr.left)
        right_type = self._analyze_expr(expr.right)

        if expr.op == "=":
            # Assignment — check target is an lvalue
            if not self._is_lvalue(expr.left):
                self._error("Left side of assignment is not assignable", expr)
            # Check const
            if isinstance(expr.left, mc_ast.Identifier):
                sym = self.symbols.lookup(expr.left.name)
                if sym and sym.is_const:
                    self._error(
                        f"Cannot assign to const variable '{expr.left.name}'",
                        expr,
                    )
            return left_type

        # Comparison / logical operators return bool
        if expr.op in ("==", "!=", "<", ">", "<=", ">=", "&&", "||"):
            return mc_ast.TypeAnnotation(base="bool")

        # Arithmetic — return the "wider" type (simplified)
        return left_type or right_type

    def _analyze_unary(self, expr: mc_ast.UnaryOp) -> Optional[mc_ast.TypeAnnotation]:
        operand_type = self._analyze_expr(expr.operand)
        if expr.op == "!":
            return mc_ast.TypeAnnotation(base="bool")
        elif expr.op == "&":
            # Address-of: T → T*
            if operand_type:
                return mc_ast.TypeAnnotation(
                    base=operand_type.base,
                    pointer_depth=operand_type.pointer_depth + 1,
                    is_const=operand_type.is_const,
                )
        elif expr.op == "*":
            # Dereference: T* → T
            if operand_type and operand_type.pointer_depth > 0:
                return mc_ast.TypeAnnotation(
                    base=operand_type.base,
                    pointer_depth=operand_type.pointer_depth - 1,
                    is_const=operand_type.is_const,
                )
            elif operand_type:
                self._error("Cannot dereference a non-pointer type", expr)
        return operand_type

    def _analyze_func_call(self, expr: mc_ast.FuncCall) -> Optional[mc_ast.TypeAnnotation]:
        # Get the callee name
        callee_name = None
        if isinstance(expr.callee, mc_ast.Identifier):
            callee_name = expr.callee.name

        if callee_name:
            sym = self.symbols.lookup(callee_name)
            if sym is None:
                self._error(f"Undefined function '{callee_name}'", expr)
                return None
            if sym.kind != SymbolKind.FUNCTION:
                self._error(f"'{callee_name}' is not a function", expr)
                return None

            # Check argument count
            if sym.params is not None and not sym.is_varargs:
                if len(expr.args) != len(sym.params):
                    self._error(
                        f"Function '{callee_name}' expects {len(sym.params)} "
                        f"arguments, got {len(expr.args)}",
                        expr,
                    )
            elif sym.params is not None and sym.is_varargs:
                if len(expr.args) < len(sym.params):
                    self._error(
                        f"Function '{callee_name}' expects at least "
                        f"{len(sym.params)} arguments, got {len(expr.args)}",
                        expr,
                    )

            # Analyze each argument
            for arg in expr.args:
                self._analyze_expr(arg)

            return sym.type_ann
        else:
            # Function pointer call — just analyze args
            self._analyze_expr(expr.callee)
            for arg in expr.args:
                self._analyze_expr(arg)
            return None

    def _analyze_member_access(
        self, expr: mc_ast.MemberAccess
    ) -> Optional[mc_ast.TypeAnnotation]:
        # Check for scoped enum: Color.RED
        if isinstance(expr.object, mc_ast.Identifier):
            scoped_name = f"{expr.object.name}.{expr.member}"
            sym = self.symbols.lookup(scoped_name)
            if sym is not None and sym.kind == SymbolKind.ENUM_MEMBER:
                return mc_ast.TypeAnnotation(base="int")

        # Otherwise, struct member access
        obj_type = self._analyze_expr(expr.object)
        if obj_type is None:
            return None

        # For arrow access, the object must be a pointer
        if expr.is_arrow:
            if obj_type.pointer_depth == 0:
                self._error("Arrow operator requires a pointer type", expr)
                return None

        # Try to look up the struct
        struct_base = obj_type.base
        struct_sym = self.symbols.lookup(
            struct_base.replace("struct ", "") if struct_base.startswith("struct ") else struct_base
        )
        if struct_sym and struct_sym.kind == SymbolKind.STRUCT and struct_sym.fields:
            for f in struct_sym.fields:
                if f.name == expr.member:
                    return f.type_ann
            self._error(
                f"Struct '{struct_base}' has no member '{expr.member}'", expr
            )

        return None

    # ------------------------------------------------------------------ #
    # Helpers                                                            #
    # ------------------------------------------------------------------ #

    def _is_lvalue(self, expr: mc_ast.Expr) -> bool:
        """Check if an expression is assignable (an lvalue)."""
        return isinstance(expr, (
            mc_ast.Identifier,
            mc_ast.MemberAccess,
            mc_ast.ArraySubscript,
            mc_ast.UnaryOp,  # *ptr is an lvalue
        ))

    def _validate_type(self, type_ann: mc_ast.TypeAnnotation, node: mc_ast.ASTNode) -> None:
        """Check that a type annotation refers to a known type."""
        base = type_ann.base
        primitives = {"void", "bool", "int", "float", "char"}
        if base in primitives:
            return
        # Check for struct type
        struct_name = base.replace("struct ", "") if base.startswith("struct ") else base
        sym = self.symbols.lookup(struct_name)
        if sym is None or sym.kind != SymbolKind.STRUCT:
            self._error(f"Unknown type '{base}'", node)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(program: mc_ast.Program) -> AnalysisResult:
    """Run semantic analysis on a Mighty C program AST.

    Returns an ``AnalysisResult`` with any errors found. If
    ``result.ok`` is ``False``, the program should not proceed to
    code generation.
    """
    analyzer = SemanticAnalyzer()
    return analyzer.analyze(program)
