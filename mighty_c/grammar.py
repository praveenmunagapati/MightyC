"""
mighty_c/grammar.py — Lark LALR(1) Grammar for Mighty C
========================================================

This module defines the complete Lark EBNF grammar string for the Mighty C
language.  The grammar is designed to be LALR(1)-compatible (no ambiguities)
and uses Lark's ``?rule`` convention to inline single-child alternatives,
keeping the resulting parse tree compact.

Operator Precedence (lowest → highest)
---------------------------------------
    assignment      =  +=  -=  *=  /=  %=
    logical_or      ||
    logical_and     &&
    bitwise_or      |
    bitwise_xor     ^
    bitwise_and     &
    equality        ==  !=
    relational      <  >  <=  >=
    shift           <<  >>
    additive        +  -
    multiplicative  *  /  %
    unary           !  -  *  &
    postfix         ()  []  .  ->
    primary         literals  identifiers  ( expr )

Design notes
------------
* Keywords are terminals with priority 2 so they take precedence over IDENT.
* C-style ``//`` and ``/* */`` comments are ignored at the lexer level.
* Pointer types are parsed as ``type_spec ("*")*``.
* ``defer`` accepts any single statement (including a block).
* ``for`` init-clause supports both var-decl and expression-statement.
"""

GRAMMAR = r"""
    // ================================================================
    // Top-level program
    // ================================================================

    start: top_level_item*

    ?top_level_item: import_stmt
                   | export_decl
                   | func_decl
                   | struct_decl
                   | enum_decl
                   | var_decl_stmt

    // ================================================================
    // Imports & Exports
    // ================================================================

    import_stmt: "import" IDENT ";"

    export_decl: "export" exportable_decl    -> export_decl

    ?exportable_decl: func_decl
                    | struct_decl
                    | enum_decl
                    | var_decl_stmt

    // ================================================================
    // Declarations
    // ================================================================

    func_decl: type_spec IDENT "(" param_list? ")" (block | ";")

    param_list: param ("," param)*
    param: type_spec IDENT

    struct_decl: "struct" IDENT "{" struct_field* "}"
    struct_field: type_spec IDENT ";"

    enum_decl: "enum" IDENT "{" enum_member_list? "}"
    enum_member_list: enum_member ("," enum_member)* ","?
    enum_member: IDENT ("=" INT_LIT)?

    // ================================================================
    // Type specifications
    // ================================================================
    // Handles: int, bool, float, char, void, const int, int*, const int**, struct Foo*

    type_spec: const_qualifier? base_type stars
    const_qualifier: "const"
    ?base_type: primitive_type | struct_type
    primitive_type: PRIM_TYPE
    PRIM_TYPE.2: "void" | "bool" | "int" | "float" | "char"
    struct_type: "struct" IDENT
    stars: STAR*
    STAR: "*"

    // ================================================================
    // Statements
    // ================================================================

    ?statement: block
              | var_decl_stmt
              | return_stmt
              | if_stmt
              | while_stmt
              | for_stmt
              | do_while_stmt
              | switch_stmt
              | break_stmt
              | continue_stmt
              | defer_stmt
              | expr_stmt

    block: "{" statement* "}"

    var_decl_stmt: type_spec IDENT ("=" expr)? ";"      -> var_decl

    return_stmt: "return" expr? ";"

    if_stmt: "if" "(" expr ")" statement ("else" statement)?

    while_stmt: "while" "(" expr ")" statement

    for_stmt: "for" "(" for_init? ";" expr? ";" expr? ")" statement
    ?for_init: for_init_decl | expr
    for_init_decl: type_spec IDENT ("=" expr)?          -> for_var_decl

    do_while_stmt: "do" statement "while" "(" expr ")" ";"

    switch_stmt: "switch" "(" expr ")" "{" case_clause* "}"
    case_clause: "case" expr ":" statement*             -> case_clause
               | "default" ":" statement*               -> default_clause

    break_stmt: "break" ";"
    continue_stmt: "continue" ";"

    defer_stmt: "defer" statement

    expr_stmt: expr ";"

    // ================================================================
    // Expressions — ordered by precedence (lowest first)
    // ================================================================
    //
    // Using ?-prefixed rules so single-child alternatives inline
    // automatically, avoiding unnecessary wrapping in the parse tree.

    ?expr: assign_expr

    ?assign_expr: logical_or_expr
                | unary_expr "=" assign_expr          -> assign_eq
                | unary_expr "+=" assign_expr         -> assign_add
                | unary_expr "-=" assign_expr         -> assign_sub
                | unary_expr "*=" assign_expr         -> assign_mul
                | unary_expr "/=" assign_expr         -> assign_div
                | unary_expr "%=" assign_expr         -> assign_mod

    ?logical_or_expr: logical_and_expr
                    | logical_or_expr "||" logical_and_expr     -> bin_or

    ?logical_and_expr: bitwise_or_expr
                     | logical_and_expr "&&" bitwise_or_expr    -> bin_and

    ?bitwise_or_expr: bitwise_xor_expr
                    | bitwise_or_expr "|" bitwise_xor_expr      -> bin_bitor

    ?bitwise_xor_expr: bitwise_and_expr
                     | bitwise_xor_expr "^" bitwise_and_expr    -> bin_bitxor

    ?bitwise_and_expr: equality_expr
                     | bitwise_and_expr "&" equality_expr       -> bin_bitand

    ?equality_expr: relational_expr
                  | equality_expr EQ_OP relational_expr          -> bin_eq

    EQ_OP: "==" | "!="

    ?relational_expr: shift_expr
                    | relational_expr REL_OP shift_expr          -> bin_rel

    REL_OP: "<=" | ">=" | "<" | ">"

    ?shift_expr: additive_expr
               | shift_expr SHIFT_OP additive_expr              -> bin_shift

    SHIFT_OP: "<<" | ">>"

    ?additive_expr: multiplicative_expr
                  | additive_expr ADD_OP multiplicative_expr     -> bin_add

    ADD_OP: "+" | "-"

    ?multiplicative_expr: unary_expr
                        | multiplicative_expr MUL_OP unary_expr -> bin_mul

    MUL_OP: "*" | "/" | "%"

    ?unary_expr: postfix_expr
               | "-" unary_expr                                 -> unary_neg
               | "!" unary_expr                                 -> unary_not
               | "*" unary_expr                                 -> unary_deref
               | "&" unary_expr                                 -> unary_addr

    ?postfix_expr: primary_expr
                 | postfix_expr "(" arg_list? ")"               -> func_call
                 | postfix_expr "[" expr "]"                    -> array_sub
                 | postfix_expr "." IDENT                       -> member_dot
                 | postfix_expr "->" IDENT                      -> member_arrow

    arg_list: expr ("," expr)*

    ?primary_expr: INT_LIT                                      -> int_lit
                 | FLOAT_LIT                                    -> float_lit
                 | CHAR_LIT                                     -> char_lit
                 | STRING_LIT                                   -> string_lit
                 | "true"                                       -> true_lit
                 | "false"                                      -> false_lit
                 | "null"                                       -> null_lit
                 | sizeof_expr
                 | IDENT                                        -> ident
                 | "(" expr ")"

    sizeof_expr: "sizeof" "(" type_spec ")"                     -> sizeof_type
               | "sizeof" "(" expr ")"                          -> sizeof_expr

    // ================================================================
    // Terminals / Tokens
    // ================================================================

    // Identifiers — lower priority than keywords (default is 0)
    IDENT: /[a-zA-Z_][a-zA-Z0-9_]*/

    // Numeric literals
    FLOAT_LIT: /[0-9]+\.[0-9]*([eE][+-]?[0-9]+)?/ | /[0-9]*\.[0-9]+([eE][+-]?[0-9]+)?/
    INT_LIT: /0[xX][0-9a-fA-F]+/ | /0[bB][01]+/ | /[0-9]+/

    // Character and string literals
    CHAR_LIT: /\'(\\.|[^\\\'])\'/
    STRING_LIT: /\"(\\.|[^\\\"])*\"/

    // Keywords — priority 2 so they beat IDENT
    // (Lark's contextual lexer handles this, but explicit priority is safer
    //  for standalone parser generation.)

    // Whitespace and comments — ignored
    %import common.WS
    %ignore WS
    LINE_COMMENT: /\/\/[^\n]*/
    BLOCK_COMMENT: /\/\*[\s\S]*?\*\//
    %ignore LINE_COMMENT
    %ignore BLOCK_COMMENT
"""
