"""
mighty_c/codegen.py — LLVM IR Code Generator for Mighty C
==========================================================

Walks the validated Pydantic AST and emits LLVM IR using ``llvmlite.ir``.

Type mapping:
    void   → ir.VoidType()
    bool   → ir.IntType(1)  (stored as i8 in memory for ABI compatibility)
    int    → ir.IntType(32)
    float  → ir.FloatType()
    char   → ir.IntType(8)
    T*     → ir.PointerType(llvm_type(T))

Design:
    * All local variables use ``alloca`` + ``load``/``store``. LLVM's
      mem2reg pass (run by clang -O1+) promotes these to SSA registers.
    * Control flow uses explicit basic blocks with ``cbranch`` / ``br``.
    * ``defer`` statements are accumulated per-scope on a stack. Before
      any terminator (``ret``, end-of-block ``br``), the deferred
      instructions are emitted in LIFO order.
    * ``export`` functions get external linkage; others get internal.
    * ``printf`` is declared as an external varargs function.
"""

from __future__ import annotations

from typing import Optional

from llvmlite import ir

from mighty_c import ast as mc_ast


# ---------------------------------------------------------------------------
# Type conversion helpers
# ---------------------------------------------------------------------------

# Cache for struct types (name → ir.LiteralStructType)
_struct_types: dict[str, ir.LiteralStructType] = {}
# Cache for struct field order (name → [field_name, ...])
_struct_fields: dict[str, list[str]] = {}
# Cache for enum values (EnumName.MEMBER → int)
_enum_values: dict[str, int] = {}


def _llvm_type(type_ann: mc_ast.TypeAnnotation) -> ir.Type:
    """Convert a Mighty C TypeAnnotation to an llvmlite IR type."""
    base = type_ann.base
    if base == "void":
        llvm_base = ir.VoidType()
    elif base == "bool":
        llvm_base = ir.IntType(1)
    elif base == "int":
        llvm_base = ir.IntType(32)
    elif base == "float":
        llvm_base = ir.FloatType()
    elif base == "char":
        llvm_base = ir.IntType(8)
    elif base.startswith("struct "):
        struct_name = base.replace("struct ", "")
        if struct_name in _struct_types:
            llvm_base = _struct_types[struct_name]
        else:
            # Forward reference — create opaque and hope it gets filled in
            llvm_base = ir.LiteralStructType([])
    else:
        # Default fallback
        llvm_base = ir.IntType(32)

    # Apply pointer indirections
    result = llvm_base
    for _ in range(type_ann.pointer_depth):
        result = ir.PointerType(result)

    return result


def _is_bool_type(t: ir.Type) -> bool:
    return isinstance(t, ir.IntType) and t.width == 1


def _is_int_type(t: ir.Type) -> bool:
    return isinstance(t, ir.IntType) and t.width == 32


def _is_float_type(t: ir.Type) -> bool:
    return isinstance(t, (ir.FloatType, ir.DoubleType))


# ---------------------------------------------------------------------------
# Code Generator
# ---------------------------------------------------------------------------

class CodeGenerator:
    """Walks the Mighty C AST and emits LLVM IR.

    Usage::

        gen = CodeGenerator()
        llvm_module = gen.generate(program_ast)
        print(llvm_module)   # LLVM IR text
    """

    def __init__(self, module_name: str = "mighty_c_module") -> None:
        self.module = ir.Module(name=module_name)
        self.module.triple = ""  # Let clang fill in the target triple

        self.builder: Optional[ir.IRBuilder] = None
        self._func: Optional[ir.Function] = None

        # Named values: variable name → alloca instruction
        self._named_values: dict[str, ir.AllocaInstr] = {}
        # Scope stack for named values (for nested scopes)
        self._scope_stack: list[dict[str, ir.AllocaInstr]] = []

        # Break/continue target stacks (for loops and switches)
        self._break_targets: list[ir.Block] = []
        self._continue_targets: list[ir.Block] = []

        # Defer stack: list of (ast_node,) per scope level
        self._defer_stack: list[list[mc_ast.Stmt]] = []

        # String literal cache: value → global variable
        self._string_cache: dict[str, ir.GlobalVariable] = {}
        self._string_counter = 0

        # Function declarations cache
        self._functions: dict[str, ir.Function] = {}

        # Clear global caches
        _struct_types.clear()
        _struct_fields.clear()
        _enum_values.clear()

    # ------------------------------------------------------------------ #
    # Public API                                                         #
    # ------------------------------------------------------------------ #

    def generate(self, program: mc_ast.Program) -> ir.Module:
        """Generate LLVM IR for a complete Mighty C program."""
        # First pass: register all top-level declarations (structs, enums, function prototypes)
        for decl in program.declarations:
            self._register_decl(decl)

        # Second pass: generate function bodies
        for decl in program.declarations:
            self._gen_decl(decl)

        return self.module

    # ------------------------------------------------------------------ #
    # Registration pass (prototypes, struct types, enum constants)        #
    # ------------------------------------------------------------------ #

    def _register_decl(self, decl: mc_ast.Decl) -> None:
        if isinstance(decl, mc_ast.StructDecl):
            self._register_struct(decl)
        elif isinstance(decl, mc_ast.EnumDecl):
            self._register_enum(decl)
        elif isinstance(decl, mc_ast.FuncDecl):
            self._register_func(decl)
        elif isinstance(decl, mc_ast.ImportStmt):
            self._register_import(decl)

    def _register_struct(self, decl: mc_ast.StructDecl) -> None:
        field_types = [_llvm_type(f.type_ann) for f in decl.fields]
        struct_type = ir.LiteralStructType(field_types)
        _struct_types[decl.name] = struct_type
        _struct_fields[decl.name] = [f.name for f in decl.fields]

    def _register_enum(self, decl: mc_ast.EnumDecl) -> None:
        for i, member in enumerate(decl.members):
            val = member.value if member.value is not None else i
            _enum_values[f"{decl.name}.{member.name}"] = val

    def _register_func(self, decl: mc_ast.FuncDecl) -> None:
        if decl.name in self._functions:
            return  # Already registered

        ret_type = _llvm_type(decl.return_type)
        param_types = [_llvm_type(p.type_ann) for p in decl.params]
        fn_type = ir.FunctionType(ret_type, param_types, var_arg=decl.is_varargs)
        fn = ir.Function(self.module, fn_type, name=decl.name)

        # Set linkage
        if not decl.is_export and decl.body is not None and decl.name != "main":
            fn.linkage = "internal"

        # Name the parameters
        for i, param in enumerate(decl.params):
            fn.args[i].name = param.name

        self._functions[decl.name] = fn

    def _register_import(self, decl: mc_ast.ImportStmt) -> None:
        """Register external functions from imported modules."""
        # For 'io' module, ensure printf is declared
        if decl.module_name == "io":
            self._ensure_printf()
        elif decl.module_name == "math":
            self._ensure_math_funcs()

    def _ensure_printf(self) -> None:
        if "printf" not in self._functions:
            # int printf(const char* fmt, ...)
            fn_type = ir.FunctionType(
                ir.IntType(32),
                [ir.PointerType(ir.IntType(8))],
                var_arg=True,
            )
            fn = ir.Function(self.module, fn_type, name="printf")
            self._functions["printf"] = fn

    def _ensure_math_funcs(self) -> None:
        for name in ("sin", "cos"):
            if name not in self._functions:
                fn_type = ir.FunctionType(ir.FloatType(), [ir.FloatType()])
                fn = ir.Function(self.module, fn_type, name=name)
                self._functions[name] = fn

    def _ensure_malloc(self) -> None:
        if "malloc" not in self._functions:
            fn_type = ir.FunctionType(
                ir.PointerType(ir.IntType(8)),
                [ir.IntType(32)],
            )
            fn = ir.Function(self.module, fn_type, name="malloc")
            self._functions["malloc"] = fn

    def _ensure_free(self) -> None:
        if "free" not in self._functions:
            fn_type = ir.FunctionType(
                ir.VoidType(),
                [ir.PointerType(ir.IntType(8))],
            )
            fn = ir.Function(self.module, fn_type, name="free")
            self._functions["free"] = fn

    # ------------------------------------------------------------------ #
    # Declaration code generation                                        #
    # ------------------------------------------------------------------ #

    def _gen_decl(self, decl: mc_ast.Decl) -> None:
        if isinstance(decl, mc_ast.FuncDecl):
            self._gen_func(decl)
        elif isinstance(decl, mc_ast.VarDecl):
            self._gen_global_var(decl)
        # Structs and enums are handled in registration pass
        # Imports are handled in registration pass

    def _gen_func(self, decl: mc_ast.FuncDecl) -> None:
        if decl.body is None:
            return  # Extern / forward declaration

        fn = self._functions[decl.name]
        self._func = fn

        # Create entry block
        entry = fn.append_basic_block("entry")
        self.builder = ir.IRBuilder(entry)

        # Push a new scope
        self._push_scope()
        self._defer_stack.append([])

        # ── Pre-allocate ALL local variables in the entry block ─────────
        # This ensures all `alloca` instructions precede any `store`/`load`,
        # which is a requirement for valid LLVM IR and for mem2reg to work.

        # 1. Allocate parameters
        for i, param in enumerate(decl.params):
            alloca = self.builder.alloca(fn.args[i].type, name=param.name)
            self._named_values[param.name] = alloca

        # 2. Pre-scan the function body and allocate all VarDecl variables
        self._pre_allocate_vars(decl.body)

        # 3. Now store parameter values (after all allocas)
        for i, param in enumerate(decl.params):
            self.builder.store(fn.args[i], self._named_values[param.name])

        # ── Generate the function body ──────────────────────────────────
        self._gen_block(decl.body)

        # If the block didn't terminate, add an implicit return
        if not self.builder.block.is_terminated:
            self._emit_defers()
            if isinstance(fn.function_type.return_type, ir.VoidType):
                self.builder.ret_void()
            else:
                # Return a zero/null default value
                self.builder.ret(self._zero_value(fn.function_type.return_type))

        self._defer_stack.pop()
        self._pop_scope()
        self._func = None
        self.builder = None

    def _pre_allocate_vars(self, node) -> None:
        """Recursively scan an AST node for VarDecl and emit allocas.

        Called once at the start of function generation so that ALL local
        variable allocas appear at the top of the entry block, before any
        other instructions.
        """
        if isinstance(node, mc_ast.VarDecl):
            llvm_type = _llvm_type(node.type_ann)
            alloca = self.builder.alloca(llvm_type, name=node.name)
            self._named_values[node.name] = alloca
        elif isinstance(node, mc_ast.Block):
            for stmt in node.stmts:
                self._pre_allocate_vars(stmt)
        elif isinstance(node, mc_ast.IfStmt):
            self._pre_allocate_vars(node.then_branch)
            if node.else_branch:
                self._pre_allocate_vars(node.else_branch)
        elif isinstance(node, mc_ast.WhileStmt):
            self._pre_allocate_vars(node.body)
        elif isinstance(node, mc_ast.ForStmt):
            if node.init:
                self._pre_allocate_vars(node.init)
            self._pre_allocate_vars(node.body)
        elif isinstance(node, mc_ast.DoWhileStmt):
            self._pre_allocate_vars(node.body)
        elif isinstance(node, mc_ast.SwitchStmt):
            for case in node.cases:
                for stmt in case.body:
                    self._pre_allocate_vars(stmt)
        elif isinstance(node, mc_ast.DeferStmt):
            self._pre_allocate_vars(node.stmt)

    def _gen_global_var(self, decl: mc_ast.VarDecl) -> None:
        """Generate a global variable."""
        llvm_type = _llvm_type(decl.type_ann)
        gv = ir.GlobalVariable(self.module, llvm_type, name=decl.name)
        gv.linkage = "internal"
        if decl.init is not None and isinstance(decl.init, mc_ast.IntLiteral):
            gv.initializer = ir.Constant(llvm_type, decl.init.value)
        else:
            gv.initializer = self._zero_value(llvm_type)

    # ------------------------------------------------------------------ #
    # Statement code generation                                          #
    # ------------------------------------------------------------------ #

    def _gen_stmt(self, stmt) -> None:
        """Generate LLVM IR for a statement."""
        if self.builder is None or self.builder.block.is_terminated:
            return  # Dead code after a terminator

        if isinstance(stmt, mc_ast.Block):
            self._push_scope()
            self._defer_stack.append([])
            self._gen_block(stmt)
            if not self.builder.block.is_terminated:
                self._emit_defers()
            self._defer_stack.pop()
            self._pop_scope()
        elif isinstance(stmt, mc_ast.VarDecl):
            self._gen_var_decl(stmt)
        elif isinstance(stmt, mc_ast.ReturnStmt):
            self._gen_return(stmt)
        elif isinstance(stmt, mc_ast.IfStmt):
            self._gen_if(stmt)
        elif isinstance(stmt, mc_ast.WhileStmt):
            self._gen_while(stmt)
        elif isinstance(stmt, mc_ast.ForStmt):
            self._gen_for(stmt)
        elif isinstance(stmt, mc_ast.DoWhileStmt):
            self._gen_do_while(stmt)
        elif isinstance(stmt, mc_ast.SwitchStmt):
            self._gen_switch(stmt)
        elif isinstance(stmt, mc_ast.BreakStmt):
            self._gen_break()
        elif isinstance(stmt, mc_ast.ContinueStmt):
            self._gen_continue()
        elif isinstance(stmt, mc_ast.DeferStmt):
            self._gen_defer(stmt)
        elif isinstance(stmt, mc_ast.ExprStmt):
            self._gen_expr(stmt.expr)

    def _gen_block(self, block: mc_ast.Block) -> None:
        for stmt in block.stmts:
            self._gen_stmt(stmt)

    def _gen_var_decl(self, decl: mc_ast.VarDecl) -> None:
        llvm_type = _llvm_type(decl.type_ann)
        # Alloca was already emitted by _pre_allocate_vars in the entry block.
        alloca = self._named_values.get(decl.name)
        if alloca is None:
            # Fallback: allocate now (shouldn't happen if _pre_allocate_vars is correct)
            alloca = self.builder.alloca(llvm_type, name=decl.name)
            self._named_values[decl.name] = alloca

        if decl.init is not None:
            init_val = self._gen_expr(decl.init)
            if init_val is not None:
                init_val = self._coerce(init_val, llvm_type)
                self.builder.store(init_val, alloca)
        else:
            # Zero-initialize
            self.builder.store(self._zero_value(llvm_type), alloca)

    def _gen_return(self, stmt: mc_ast.ReturnStmt) -> None:
        # Emit all deferred statements before returning (LIFO order, all scopes)
        self._emit_all_defers()

        if stmt.value is not None:
            val = self._gen_expr(stmt.value)
            if val is not None:
                ret_type = self._func.function_type.return_type
                val = self._coerce(val, ret_type)
                self.builder.ret(val)
            else:
                self.builder.ret_void()
        else:
            self.builder.ret_void()

    def _gen_if(self, stmt: mc_ast.IfStmt) -> None:
        cond = self._gen_expr(stmt.condition)
        if cond is None:
            return

        cond = self._to_bool(cond)

        then_bb = self._func.append_basic_block("if.then")
        else_bb = self._func.append_basic_block("if.else") if stmt.else_branch else None
        merge_bb = self._func.append_basic_block("if.merge")

        if else_bb:
            self.builder.cbranch(cond, then_bb, else_bb)
        else:
            self.builder.cbranch(cond, then_bb, merge_bb)

        # Then branch
        self.builder.position_at_end(then_bb)
        self._gen_stmt(stmt.then_branch)
        if not self.builder.block.is_terminated:
            self.builder.branch(merge_bb)

        # Else branch
        if else_bb:
            self.builder.position_at_end(else_bb)
            self._gen_stmt(stmt.else_branch)
            if not self.builder.block.is_terminated:
                self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _gen_while(self, stmt: mc_ast.WhileStmt) -> None:
        cond_bb = self._func.append_basic_block("while.cond")
        body_bb = self._func.append_basic_block("while.body")
        after_bb = self._func.append_basic_block("while.after")

        self.builder.branch(cond_bb)

        # Condition
        self.builder.position_at_end(cond_bb)
        cond = self._gen_expr(stmt.condition)
        if cond is not None:
            cond = self._to_bool(cond)
            self.builder.cbranch(cond, body_bb, after_bb)
        else:
            self.builder.branch(after_bb)

        # Body
        self._break_targets.append(after_bb)
        self._continue_targets.append(cond_bb)

        self.builder.position_at_end(body_bb)
        self._gen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        self._break_targets.pop()
        self._continue_targets.pop()

        self.builder.position_at_end(after_bb)

    def _gen_for(self, stmt: mc_ast.ForStmt) -> None:
        self._push_scope()

        # Init
        if stmt.init is not None:
            self._gen_stmt(stmt.init)

        cond_bb = self._func.append_basic_block("for.cond")
        body_bb = self._func.append_basic_block("for.body")
        update_bb = self._func.append_basic_block("for.update")
        after_bb = self._func.append_basic_block("for.after")

        self.builder.branch(cond_bb)

        # Condition
        self.builder.position_at_end(cond_bb)
        if stmt.condition is not None:
            cond = self._gen_expr(stmt.condition)
            if cond is not None:
                cond = self._to_bool(cond)
                self.builder.cbranch(cond, body_bb, after_bb)
            else:
                self.builder.branch(body_bb)
        else:
            self.builder.branch(body_bb)  # Infinite loop

        # Body
        self._break_targets.append(after_bb)
        self._continue_targets.append(update_bb)

        self.builder.position_at_end(body_bb)
        self._gen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(update_bb)

        self._break_targets.pop()
        self._continue_targets.pop()

        # Update
        self.builder.position_at_end(update_bb)
        if stmt.update is not None:
            self._gen_expr(stmt.update)
        self.builder.branch(cond_bb)

        self.builder.position_at_end(after_bb)
        self._pop_scope()

    def _gen_do_while(self, stmt: mc_ast.DoWhileStmt) -> None:
        body_bb = self._func.append_basic_block("dowhile.body")
        cond_bb = self._func.append_basic_block("dowhile.cond")
        after_bb = self._func.append_basic_block("dowhile.after")

        self.builder.branch(body_bb)

        # Body
        self._break_targets.append(after_bb)
        self._continue_targets.append(cond_bb)

        self.builder.position_at_end(body_bb)
        self._gen_stmt(stmt.body)
        if not self.builder.block.is_terminated:
            self.builder.branch(cond_bb)

        self._break_targets.pop()
        self._continue_targets.pop()

        # Condition
        self.builder.position_at_end(cond_bb)
        cond = self._gen_expr(stmt.condition)
        if cond is not None:
            cond = self._to_bool(cond)
            self.builder.cbranch(cond, body_bb, after_bb)
        else:
            self.builder.branch(after_bb)

        self.builder.position_at_end(after_bb)

    def _gen_switch(self, stmt: mc_ast.SwitchStmt) -> None:
        val = self._gen_expr(stmt.expr)
        if val is None:
            return

        after_bb = self._func.append_basic_block("switch.after")
        default_bb = after_bb  # Default falls through to after

        # Create blocks for each case
        case_blocks: list[tuple[Optional[ir.Value], ir.Block]] = []
        for case in stmt.cases:
            bb = self._func.append_basic_block("switch.case")
            if case.value is None:
                default_bb = bb
            case_blocks.append((case, bb))

        # Create the switch instruction
        switch = self.builder.switch(val, default_bb)

        self._break_targets.append(after_bb)

        for (case, bb) in case_blocks:
            if case.value is not None:
                case_val = self._gen_const_expr(case.value)
                if case_val is not None:
                    switch.add_case(case_val, bb)

            self.builder.position_at_end(bb)
            for s in case.body:
                self._gen_stmt(s)
            if not self.builder.block.is_terminated:
                # Fall through to after (no implicit fallthrough between cases)
                self.builder.branch(after_bb)

        self._break_targets.pop()
        self.builder.position_at_end(after_bb)

    def _gen_break(self) -> None:
        if self._break_targets:
            self._emit_defers()
            self.builder.branch(self._break_targets[-1])

    def _gen_continue(self) -> None:
        if self._continue_targets:
            self.builder.branch(self._continue_targets[-1])

    def _gen_defer(self, stmt: mc_ast.DeferStmt) -> None:
        """Push a deferred statement onto the current scope's defer stack."""
        if self._defer_stack:
            self._defer_stack[-1].append(stmt.stmt)

    # ------------------------------------------------------------------ #
    # Expression code generation                                         #
    # ------------------------------------------------------------------ #

    def _gen_expr(self, expr: mc_ast.Expr) -> Optional[ir.Value]:
        """Generate LLVM IR for an expression and return its value."""
        if isinstance(expr, mc_ast.IntLiteral):
            return ir.Constant(ir.IntType(32), expr.value)
        elif isinstance(expr, mc_ast.FloatLiteral):
            return ir.Constant(ir.FloatType(), expr.value)
        elif isinstance(expr, mc_ast.BoolLiteral):
            return ir.Constant(ir.IntType(1), int(expr.value))
        elif isinstance(expr, mc_ast.CharLiteral):
            return ir.Constant(ir.IntType(8), ord(expr.value))
        elif isinstance(expr, mc_ast.StringLiteral):
            return self._gen_string_literal(expr.value)
        elif isinstance(expr, mc_ast.NullLiteral):
            return ir.Constant(ir.PointerType(ir.IntType(8)), None)
        elif isinstance(expr, mc_ast.Identifier):
            return self._gen_identifier(expr)
        elif isinstance(expr, mc_ast.BinOp):
            return self._gen_binop(expr)
        elif isinstance(expr, mc_ast.UnaryOp):
            return self._gen_unaryop(expr)
        elif isinstance(expr, mc_ast.FuncCall):
            return self._gen_func_call(expr)
        elif isinstance(expr, mc_ast.MemberAccess):
            return self._gen_member_access(expr)
        elif isinstance(expr, mc_ast.ArraySubscript):
            return self._gen_array_subscript(expr)
        elif isinstance(expr, mc_ast.SizeOf):
            return self._gen_sizeof(expr)
        return None

    def _gen_string_literal(self, value: str) -> ir.Value:
        """Create a global string constant and return a pointer to it."""
        if value in self._string_cache:
            gv = self._string_cache[value]
        else:
            encoded = (value + "\0").encode("utf-8")
            str_type = ir.ArrayType(ir.IntType(8), len(encoded))
            gv = ir.GlobalVariable(self.module, str_type, name=f".str.{self._string_counter}")
            gv.global_constant = True
            gv.linkage = "internal"
            gv.initializer = ir.Constant(str_type, bytearray(encoded))
            self._string_cache[value] = gv
            self._string_counter += 1

        # Return a pointer to the first element (i8*)
        zero = ir.Constant(ir.IntType(32), 0)
        return self.builder.gep(gv, [zero, zero], inbounds=True, name="str")

    def _gen_identifier(self, expr: mc_ast.Identifier) -> Optional[ir.Value]:
        """Load a variable's value from its alloca."""
        name = expr.name

        # Check local variables
        alloca = self._named_values.get(name)
        if alloca is not None:
            return self.builder.load(alloca, name=name)

        # Check if it's a function
        if name in self._functions:
            return self._functions[name]

        # Check global variables
        for gv in self.module.global_variables:
            if gv.name == name:
                return self.builder.load(gv, name=name)

        return None

    def _gen_binop(self, expr: mc_ast.BinOp) -> Optional[ir.Value]:
        # Handle assignment specially
        if expr.op == "=":
            return self._gen_assignment(expr)

        left = self._gen_expr(expr.left)
        right = self._gen_expr(expr.right)
        if left is None or right is None:
            return None

        # Coerce types to match
        left, right = self._coerce_pair(left, right)

        op = expr.op

        if _is_float_type(left.type):
            return self._gen_float_binop(op, left, right)
        else:
            return self._gen_int_binop(op, left, right)

    def _gen_int_binop(self, op: str, left: ir.Value, right: ir.Value) -> Optional[ir.Value]:
        """Generate integer binary operation."""
        if op == "+":
            return self.builder.add(left, right, name="add")
        elif op == "-":
            return self.builder.sub(left, right, name="sub")
        elif op == "*":
            return self.builder.mul(left, right, name="mul")
        elif op == "/":
            return self.builder.sdiv(left, right, name="div")
        elif op == "%":
            return self.builder.srem(left, right, name="mod")
        elif op == "&":
            return self.builder.and_(left, right, name="bitand")
        elif op == "|":
            return self.builder.or_(left, right, name="bitor")
        elif op == "^":
            return self.builder.xor(left, right, name="bitxor")
        elif op == "<<":
            return self.builder.shl(left, right, name="shl")
        elif op == ">>":
            return self.builder.ashr(left, right, name="shr")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            cmp_map = {
                "==": "==", "!=": "!=",
                "<": "<", ">": ">",
                "<=": "<=", ">=": ">=",
            }
            return self.builder.icmp_signed(cmp_map[op], left, right, name="cmp")
        elif op == "&&":
            # Short-circuit AND
            left_bool = self._to_bool(left)
            right_bool = self._to_bool(right)
            return self.builder.and_(left_bool, right_bool, name="land")
        elif op == "||":
            # Short-circuit OR
            left_bool = self._to_bool(left)
            right_bool = self._to_bool(right)
            return self.builder.or_(left_bool, right_bool, name="lor")
        return None

    def _gen_float_binop(self, op: str, left: ir.Value, right: ir.Value) -> Optional[ir.Value]:
        """Generate floating-point binary operation."""
        if op == "+":
            return self.builder.fadd(left, right, name="fadd")
        elif op == "-":
            return self.builder.fsub(left, right, name="fsub")
        elif op == "*":
            return self.builder.fmul(left, right, name="fmul")
        elif op == "/":
            return self.builder.fdiv(left, right, name="fdiv")
        elif op == "%":
            return self.builder.frem(left, right, name="fmod")
        elif op in ("==", "!=", "<", ">", "<=", ">="):
            cmp_map = {
                "==": "==", "!=": "!=",
                "<": "<", ">": ">",
                "<=": "<=", ">=": ">=",
            }
            return self.builder.fcmp_ordered(cmp_map[op], left, right, name="fcmp")
        return None

    def _gen_assignment(self, expr: mc_ast.BinOp) -> Optional[ir.Value]:
        """Generate assignment: store value into the target's alloca."""
        val = self._gen_expr(expr.right)
        if val is None:
            return None

        target = expr.left

        if isinstance(target, mc_ast.Identifier):
            alloca = self._named_values.get(target.name)
            if alloca is None:
                # Try global
                for gv in self.module.global_variables:
                    if gv.name == target.name:
                        alloca = gv
                        break
            if alloca is not None:
                val = self._coerce(val, alloca.type.pointee)
                self.builder.store(val, alloca)
                return val
        elif isinstance(target, mc_ast.MemberAccess):
            ptr = self._gen_member_ptr(target)
            if ptr is not None:
                val = self._coerce(val, ptr.type.pointee)
                self.builder.store(val, ptr)
                return val
        elif isinstance(target, mc_ast.UnaryOp) and target.op == "*":
            # *ptr = val
            ptr = self._gen_expr(target.operand)
            if ptr is not None:
                val = self._coerce(val, ptr.type.pointee)
                self.builder.store(val, ptr)
                return val
        elif isinstance(target, mc_ast.ArraySubscript):
            ptr = self._gen_subscript_ptr(target)
            if ptr is not None:
                val = self._coerce(val, ptr.type.pointee)
                self.builder.store(val, ptr)
                return val

        return val

    def _gen_unaryop(self, expr: mc_ast.UnaryOp) -> Optional[ir.Value]:
        if expr.op == "&":
            # Address-of: return the alloca pointer directly
            if isinstance(expr.operand, mc_ast.Identifier):
                alloca = self._named_values.get(expr.operand.name)
                if alloca is not None:
                    return alloca
            return None

        operand = self._gen_expr(expr.operand)
        if operand is None:
            return None

        if expr.op == "-":
            if _is_float_type(operand.type):
                return self.builder.fneg(operand, name="neg")
            else:
                return self.builder.neg(operand, name="neg")
        elif expr.op == "!":
            bool_val = self._to_bool(operand)
            return self.builder.not_(bool_val, name="not")
        elif expr.op == "*":
            # Dereference
            if isinstance(operand.type, ir.PointerType):
                return self.builder.load(operand, name="deref")

        return None

    def _gen_func_call(self, expr: mc_ast.FuncCall) -> Optional[ir.Value]:
        # Get the function
        callee_name = None
        if isinstance(expr.callee, mc_ast.Identifier):
            callee_name = expr.callee.name

        if callee_name:
            # Ensure known externals are declared
            if callee_name == "printf":
                self._ensure_printf()
            elif callee_name == "malloc":
                self._ensure_malloc()
            elif callee_name == "free":
                self._ensure_free()

            fn = self._functions.get(callee_name)
            if fn is None:
                return None

            # Generate arguments
            args = []
            for i, arg_expr in enumerate(expr.args):
                arg_val = self._gen_expr(arg_expr)
                if arg_val is None:
                    return None

                # Coerce argument to expected type (for non-varargs params)
                if i < len(fn.function_type.args):
                    expected_type = fn.function_type.args[i]
                    arg_val = self._coerce(arg_val, expected_type)
                else:
                    # Varargs: promote float to double? For now, leave as-is
                    pass

                args.append(arg_val)

            # Call the function
            if isinstance(fn.function_type.return_type, ir.VoidType):
                self.builder.call(fn, args)
                return None
            else:
                return self.builder.call(fn, args, name="call")

        return None

    def _gen_member_access(self, expr: mc_ast.MemberAccess) -> Optional[ir.Value]:
        # Check for enum member: Color.RED
        if isinstance(expr.object, mc_ast.Identifier):
            key = f"{expr.object.name}.{expr.member}"
            if key in _enum_values:
                return ir.Constant(ir.IntType(32), _enum_values[key])

        # Struct member access
        ptr = self._gen_member_ptr(expr)
        if ptr is not None:
            return self.builder.load(ptr, name=f"member.{expr.member}")
        return None

    def _gen_member_ptr(self, expr: mc_ast.MemberAccess) -> Optional[ir.Value]:
        """Get a pointer to a struct member (for both load and store)."""
        if isinstance(expr.object, mc_ast.Identifier):
            name = expr.object.name
            alloca = self._named_values.get(name)
            if alloca is None:
                return None

            if expr.is_arrow:
                # ptr->member: load the pointer, then GEP
                ptr = self.builder.load(alloca, name=f"load.{name}")
                struct_type = ptr.type.pointee
            else:
                # obj.member: GEP directly into the alloca
                struct_type = alloca.type.pointee
                ptr = alloca

            # Find field index
            if isinstance(struct_type, ir.LiteralStructType):
                struct_name = self._find_struct_name(struct_type)
                if struct_name and struct_name in _struct_fields:
                    fields = _struct_fields[struct_name]
                    if expr.member in fields:
                        idx = fields.index(expr.member)
                        zero = ir.Constant(ir.IntType(32), 0)
                        field_idx = ir.Constant(ir.IntType(32), idx)
                        return self.builder.gep(ptr, [zero, field_idx], name=f"field.{expr.member}")
        return None

    def _gen_array_subscript(self, expr: mc_ast.ArraySubscript) -> Optional[ir.Value]:
        ptr = self._gen_subscript_ptr(expr)
        if ptr is not None:
            return self.builder.load(ptr, name="subscript")
        return None

    def _gen_subscript_ptr(self, expr: mc_ast.ArraySubscript) -> Optional[ir.Value]:
        """Get a pointer to an array element."""
        array_val = self._gen_expr(expr.array)
        index_val = self._gen_expr(expr.index)
        if array_val is None or index_val is None:
            return None

        if isinstance(array_val.type, ir.PointerType):
            return self.builder.gep(array_val, [index_val], name="elem.ptr")
        return None

    def _gen_sizeof(self, expr: mc_ast.SizeOf) -> ir.Value:
        """Generate sizeof as a constant integer."""
        if expr.target_type is not None:
            llvm_t = _llvm_type(expr.target_type)
        else:
            # sizeof(expr) — we'd need type inference here
            # For simplicity, return 4 (sizeof(int))
            return ir.Constant(ir.IntType(32), 4)

        # Compute size based on LLVM type
        size = self._sizeof_llvm_type(llvm_t)
        return ir.Constant(ir.IntType(32), size)

    def _gen_const_expr(self, expr: mc_ast.Expr) -> Optional[ir.Constant]:
        """Evaluate a constant expression for switch case values."""
        if isinstance(expr, mc_ast.IntLiteral):
            return ir.Constant(ir.IntType(32), expr.value)
        elif isinstance(expr, mc_ast.MemberAccess):
            if isinstance(expr.object, mc_ast.Identifier):
                key = f"{expr.object.name}.{expr.member}"
                if key in _enum_values:
                    return ir.Constant(ir.IntType(32), _enum_values[key])
        return None

    # ------------------------------------------------------------------ #
    # Defer helpers                                                      #
    # ------------------------------------------------------------------ #

    def _emit_defers(self) -> None:
        """Emit deferred statements for the current scope (LIFO order)."""
        if self._defer_stack:
            for stmt in reversed(self._defer_stack[-1]):
                self._gen_stmt(stmt)

    def _emit_all_defers(self) -> None:
        """Emit ALL deferred statements from all scopes (for return)."""
        for scope_defers in reversed(self._defer_stack):
            for stmt in reversed(scope_defers):
                self._gen_stmt(stmt)

    # ------------------------------------------------------------------ #
    # Scope management                                                   #
    # ------------------------------------------------------------------ #

    def _push_scope(self) -> None:
        self._scope_stack.append(dict(self._named_values))

    def _pop_scope(self) -> None:
        if self._scope_stack:
            self._named_values = self._scope_stack.pop()

    # ------------------------------------------------------------------ #
    # Utility helpers                                                    #
    # ------------------------------------------------------------------ #

    def _create_alloca(self, llvm_type: ir.Type, name: str) -> ir.AllocaInstr:
        """Create an alloca in the function's entry block.

        Placing all allocas at the top of the entry block ensures that
        LLVM's mem2reg pass can promote them to SSA registers.
        """
        entry_block = self._func.entry_basic_block
        builder = ir.IRBuilder(entry_block)
        # Position at the beginning of the entry block
        if entry_block.instructions:
            builder.position_before(entry_block.instructions[0])
        return builder.alloca(llvm_type, name=name)

    def _zero_value(self, llvm_type: ir.Type) -> ir.Constant:
        """Create a zero-initialized constant for any LLVM type.

        Handles structs (which need element-wise zero init) and pointers
        (which need ``null``) in addition to simple scalar zero.
        """
        if isinstance(llvm_type, ir.IntType):
            return ir.Constant(llvm_type, 0)
        elif isinstance(llvm_type, (ir.FloatType, ir.DoubleType)):
            return ir.Constant(llvm_type, 0.0)
        elif isinstance(llvm_type, ir.PointerType):
            return ir.Constant(llvm_type, None)
        elif isinstance(llvm_type, ir.LiteralStructType):
            zeros = [self._zero_value(elem) for elem in llvm_type.elements]
            return ir.Constant.literal_struct(zeros)
        elif isinstance(llvm_type, ir.ArrayType):
            return ir.Constant(llvm_type, [self._zero_value(llvm_type.element)] * llvm_type.count)
        elif isinstance(llvm_type, ir.VoidType):
            # Shouldn't happen, but handle gracefully
            return ir.Constant(ir.IntType(32), 0)
        else:
            return ir.Constant(llvm_type, 0)

    def _to_bool(self, val: ir.Value) -> ir.Value:
        """Convert a value to i1 (boolean)."""
        if _is_bool_type(val.type):
            return val
        if isinstance(val.type, ir.IntType):
            zero = ir.Constant(val.type, 0)
            return self.builder.icmp_signed("!=", val, zero, name="tobool")
        if _is_float_type(val.type):
            zero = ir.Constant(val.type, 0.0)
            return self.builder.fcmp_ordered("!=", val, zero, name="tobool")
        if isinstance(val.type, ir.PointerType):
            null = ir.Constant(val.type, None)
            return self.builder.icmp_unsigned("!=", val, null, name="tobool")
        return val

    def _coerce(self, val: ir.Value, target_type: ir.Type) -> ir.Value:
        """Coerce a value to a target LLVM type."""
        if val.type == target_type:
            return val

        # Bool (i1) to int (i32)
        if _is_bool_type(val.type) and isinstance(target_type, ir.IntType):
            return self.builder.zext(val, target_type, name="zext")

        # Int to bool
        if isinstance(val.type, ir.IntType) and _is_bool_type(target_type):
            return self._to_bool(val)

        # Int to int (different widths)
        if isinstance(val.type, ir.IntType) and isinstance(target_type, ir.IntType):
            if val.type.width < target_type.width:
                return self.builder.sext(val, target_type, name="sext")
            elif val.type.width > target_type.width:
                return self.builder.trunc(val, target_type, name="trunc")

        # Int to float
        if isinstance(val.type, ir.IntType) and _is_float_type(target_type):
            return self.builder.sitofp(val, target_type, name="itof")

        # Float to int
        if _is_float_type(val.type) and isinstance(target_type, ir.IntType):
            return self.builder.fptosi(val, target_type, name="ftoi")

        # Pointer types — bitcast if needed
        if isinstance(val.type, ir.PointerType) and isinstance(target_type, ir.PointerType):
            return self.builder.bitcast(val, target_type, name="ptrcast")

        return val

    def _coerce_pair(self, left: ir.Value, right: ir.Value) -> tuple[ir.Value, ir.Value]:
        """Coerce a pair of values to a common type."""
        if left.type == right.type:
            return left, right

        # If one is float, promote the other
        if _is_float_type(left.type) and isinstance(right.type, ir.IntType):
            right = self.builder.sitofp(right, left.type, name="promote")
            return left, right
        if _is_float_type(right.type) and isinstance(left.type, ir.IntType):
            left = self.builder.sitofp(left, right.type, name="promote")
            return left, right

        # If both are int but different widths, extend the smaller
        if isinstance(left.type, ir.IntType) and isinstance(right.type, ir.IntType):
            if left.type.width < right.type.width:
                left = self.builder.sext(left, right.type, name="widen")
            elif right.type.width < left.type.width:
                right = self.builder.sext(right, left.type, name="widen")

        return left, right

    def _sizeof_llvm_type(self, t: ir.Type) -> int:
        """Estimate the size of an LLVM type in bytes."""
        if isinstance(t, ir.IntType):
            return max(1, t.width // 8)
        elif isinstance(t, ir.FloatType):
            return 4
        elif isinstance(t, ir.DoubleType):
            return 8
        elif isinstance(t, ir.PointerType):
            return 8  # 64-bit pointers
        elif isinstance(t, ir.LiteralStructType):
            return sum(self._sizeof_llvm_type(e) for e in t.elements)
        elif isinstance(t, ir.VoidType):
            return 0
        return 4

    def _find_struct_name(self, struct_type: ir.LiteralStructType) -> Optional[str]:
        """Reverse-lookup: find the struct name for a given LLVM struct type."""
        for name, st in _struct_types.items():
            if st is struct_type or (st.elements == struct_type.elements):
                return name
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate(program: mc_ast.Program, module_name: str = "mighty_c_module") -> ir.Module:
    """Generate LLVM IR for a Mighty C program.

    Returns an ``llvmlite.ir.Module`` whose ``str()`` is valid LLVM IR text.
    """
    gen = CodeGenerator(module_name=module_name)
    return gen.generate(program)
