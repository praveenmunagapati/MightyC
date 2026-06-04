// =========================================================================
// mighty_c_compiler.mc — A Self-Hosting Mighty C Compiler in Mighty C
// =========================================================================
// This compiler compiles a subset of Mighty C to textual LLVM IR.
// The subset contains: functions, variable declarations, while loops,
// if/else branches, return statements, pointer dereferences, pointer
// arithmetic, assignments, binary operations, and function calls.
// =========================================================================

import io;

// External standard C library declarations
char* fopen(char* filename, char* mode);
int fread(char* buffer, int size, int count, char* stream);
int fclose(char* stream);
void exit(int status);
int sprintf(char* str, char* format, int arg1);
int fflush(char* stream);

// Global variables for Lexer
char* src;
char* token_str;
int token_type;
int token_val;

// Token types
int TOK_EOF;
int TOK_IDENT;
int TOK_INT;
int TOK_STRING;
int TOK_CHAR;
int TOK_SYMBOL;

// Keywords
int TOK_INT_KEY;
int TOK_CHAR_KEY;
int TOK_VOID_KEY;
int TOK_IF;
int TOK_ELSE;
int TOK_WHILE;
int TOK_RETURN;
int TOK_IMPORT;
int TOK_EXPORT;
int TOK_NULL;
int TOK_STRUCT;
int TOK_BREAK;
int TOK_CONTINUE;
int block_terminated;

// Helpers
int str_len(char* s) {
    int len = 0;
    while (*(s + len) != 0) {
        len = len + 1;
    }
    return len;
}

int str_compare(char* a, char* b, int len) {
    int i = 0;
    while (i < len) {
        if (*(a + i) != *(b + i)) {
            return 0;
        }
        i = i + 1;
    }
    return 1;
}

int is_space(char c) {
    return (c == 32) || (c == 9) || (c == 10) || (c == 13); // space, tab, newline, carriage return
}

int is_alpha(char c) {
    return ((c >= 65) && (c <= 90)) || ((c >= 97) && (c <= 122)) || (c == 95); // A-Z, a-z, _
}

int is_num(char c) {
    return (c >= 48) && (c <= 57); // 0-9
}

int is_alnum(char c) {
    return is_alpha(c) || is_num(c);
}

int is_void_func(char* name) {
    if ((str_compare(name, "next_token", 10)) && (str_len(name) == 10)) { return 1; }
    if ((str_compare(name, "next_token_impl", 15)) && (str_len(name) == 15)) { return 1; }
    if ((str_compare(name, "expect_symbol", 13)) && (str_len(name) == 13)) { return 1; }
    if ((str_compare(name, "add_local_var", 13)) && (str_len(name) == 13)) { return 1; }
    if ((str_compare(name, "add_global_var", 14)) && (str_len(name) == 14)) { return 1; }
    if ((str_compare(name, "print_val_ref", 13)) && (str_len(name) == 13)) { return 1; }
    if ((str_compare(name, "print_hex_byte", 14)) && (str_len(name) == 14)) { return 1; }
    if ((str_compare(name, "parse_block", 11)) && (str_len(name) == 11)) { return 1; }
    if ((str_compare(name, "parse_stmt", 10)) && (str_len(name) == 10)) { return 1; }
    if ((str_compare(name, "parse_top_level", 15)) && (str_len(name) == 15)) { return 1; }
    if ((str_compare(name, "exit", 4)) && (str_len(name) == 4)) { return 1; }
    if ((str_compare(name, "free", 4)) && (str_len(name) == 4)) { return 1; }
    return 0;
}

char* unescape_string(char* s, int len) {
    char* dest = malloc(len + 1);
    int i = 0;
    int j = 0;
    while (i < len) {
        char c = *(s + i);
        if ((c == 92) && ((i + 1) < len)) { // backslash
            char next = *(s + i + 1);
            if (next == 110) { // 'n'
                *(dest + j) = 10;
            } else if (next == 116) { // 't'
                *(dest + j) = 9;
            } else if (next == 114) { // 'r'
                *(dest + j) = 13;
            } else if (next == 92) { // '\\'
                *(dest + j) = 92;
            } else if (next == 34) { // '\"'
                *(dest + j) = 34;
            } else if (next == 48) { // '\0'
                *(dest + j) = 0;
            } else {
                *(dest + j) = next;
            }
            i = i + 2;
        } else {
            *(dest + j) = c;
            i = i + 1;
        }
        j = j + 1;
    }
    *(dest + j) = 0;
    return dest;
}

void print_hex_byte(char c) {
    int val = c;
    if (val < 0) {
        val = val + 256;
    }
    int h1 = val / 16;
    int h2 = val - (h1 * 16);
    printf("\\");
    if (h1 < 10) {
        printf("%c", h1 + 48);
    } else {
        printf("%c", h1 - 10 + 65);
    }
    if (h2 < 10) {
        printf("%c", h2 + 48);
    } else {
        printf("%c", h2 - 10 + 65);
    }
}

// Memory allocator for strings
char* str_copy(char* s, int len) {
    char* copy = malloc(len + 1);
    int i = 0;
    while (i < len) {
        *(copy + i) = *(s + i);
        i = i + 1;
    }
    *(copy + len) = 0;
    return copy;
}

// Lexer get next token implementation
void next_token_impl() {
    // Skip spaces and comments
    int keep_looping = 1;
    while ((*src != 0) && keep_looping) {
        if (is_space(*src)) {
            src = src + 1;
        } else if ((*src == 47) && (*(src + 1) == 47)) { // "//" comment
            src = src + 2;
            while (((*src != 10) && (*src != 13)) && (*src != 0)) {
                src = src + 1;
            }
        } else if ((*src == 47) && (*(src + 1) == 42)) { // "/*" comment
            src = src + 2;
            int in_comment = 1;
            while ((*src != 0) && in_comment) {
                if ((*src == 42) && (*(src + 1) == 47)) {
                    src = src + 2;
                    in_comment = 0;
                } else {
                    src = src + 1;
                }
            }
        } else {
            keep_looping = 0;
        }
    }

    if (*src == 0) {
        token_type = TOK_EOF;
        token_str = "EOF";
        return;
    }

    // Numbers
    if (is_num(*src)) {
        int val = 0;
        char* start_num = src;
        while (is_num(*src)) {
            val = val * 10 + (*src - 48);
            src = src + 1;
        }
        token_type = TOK_INT;
        token_val = val;
        token_str = str_copy(start_num, src - start_num);
        return;
    }

    // Identifiers & Keywords
    if (is_alpha(*src)) {
        char* start_ident = src;
        while (is_alnum(*src)) {
            src = src + 1;
        }
        int len_ident = src - start_ident;
        char* name = str_copy(start_ident, len_ident);

        token_str = name;
        token_type = TOK_IDENT;

        // Check keywords
        if ((len_ident == 3) && (str_compare(name, "int", 3))) { token_type = TOK_INT_KEY; }
        if ((len_ident == 4) && (str_compare(name, "char", 4))) { token_type = TOK_CHAR_KEY; }
        if ((len_ident == 4) && (str_compare(name, "void", 4))) { token_type = TOK_VOID_KEY; }
        if ((len_ident == 2) && (str_compare(name, "if", 2))) { token_type = TOK_IF; }
        if ((len_ident == 4) && (str_compare(name, "else", 4))) { token_type = TOK_ELSE; }
        if ((len_ident == 5) && (str_compare(name, "while", 5))) { token_type = TOK_WHILE; }
        if ((len_ident == 6) && (str_compare(name, "return", 6))) { token_type = TOK_RETURN; }
        if ((len_ident == 6) && (str_compare(name, "import", 6))) { token_type = TOK_IMPORT; }
        if ((len_ident == 6) && (str_compare(name, "export", 6))) { token_type = TOK_EXPORT; }
        if ((len_ident == 4) && (str_compare(name, "null", 4))) { token_type = TOK_NULL; }
        if ((len_ident == 6) && (str_compare(name, "struct", 6))) { token_type = TOK_STRUCT; }
        if ((len_ident == 5) && (str_compare(name, "break", 5))) { token_type = TOK_BREAK; }
        if ((len_ident == 8) && (str_compare(name, "continue", 8))) { token_type = TOK_CONTINUE; }

        return;
    }

    // Strings
    if (*src == 34) { // '"'
        src = src + 1;
        char* start_str = src;
        while ((*src != 34) && (*src != 0)) {
            // Handle escaped characters if any
            src = src + 1;
        }
        int len_str = src - start_str;
        token_str = unescape_string(start_str, len_str);
        token_type = TOK_STRING;
        if (*src == 34) {
            src = src + 1;
        }
        return;
    }

    // Multi-character operators
    if ((*src == 61) && (*(src + 1) == 61)) { // "=="
        src = src + 2;
        token_str = "==";
        token_type = TOK_SYMBOL;
        return;
    }
    if ((*src == 33) && (*(src + 1) == 61)) { // "!="
        src = src + 2;
        token_str = "!=";
        token_type = TOK_SYMBOL;
        return;
    }
    if ((*src == 60) && (*(src + 1) == 61)) { // "<="
        src = src + 2;
        token_str = "<=";
        token_type = TOK_SYMBOL;
        return;
    }
    if ((*src == 62) && (*(src + 1) == 61)) { // ">="
        src = src + 2;
        token_str = ">=";
        token_type = TOK_SYMBOL;
        return;
    }
    if ((*src == 38) && (*(src + 1) == 38)) { // "&&"
        src = src + 2;
        token_str = "&&";
        token_type = TOK_SYMBOL;
        return;
    }
    if ((*src == 124) && (*(src + 1) == 124)) { // "||"
        src = src + 2;
        token_str = "||";
        token_type = TOK_SYMBOL;
        return;
    }

    // Single-character symbols
    char* sym = str_copy(src, 1);
    src = src + 1;
    token_str = sym;
    token_type = TOK_SYMBOL;
}

void next_token() {
    next_token_impl();
    printf("; token len: %d type: %d\n", str_len(token_str), token_type);
    fflush(0);
}

int match_token(char* s) {
    int len = str_len(s);
    if (str_len(token_str) != len) {
        return 0;
    }
    return str_compare(token_str, s, len);
}

int match_op(char* op, char* s) {
    int len = str_len(s);
    if (str_len(op) != len) {
        return 0;
    }
    return str_compare(op, s, len);
}

void expect_symbol(char* s) {
    if ((token_type != TOK_SYMBOL) || (match_token(s) == 0)) {
        printf("; error: Expected symbol '%s', got '%s'\n", s, token_str);
        exit(1);
    }
    next_token();
}

// Global register counter and label counter
int reg_counter;
int label_counter;

int next_reg() {
    reg_counter = reg_counter + 1;
    return reg_counter;
}

int next_label() {
    label_counter = label_counter + 1;
    return label_counter;
}

// Global loop exit/cond label stacks
int* break_stack;
int* continue_stack;
int loop_depth;

// Global scope tracker for variables
char** local_vars;
int* local_var_byte;
int local_var_count;

// Global variable type tracking
char** global_vars;
int* global_var_byte;
int global_var_count;

void add_local_var(char* name, int is_byte) {
    *(local_vars + local_var_count) = name;
    *(local_var_byte + local_var_count) = is_byte;
    local_var_count = local_var_count + 1;
}

void add_global_var(char* name, int is_byte) {
    *(global_vars + global_var_count) = name;
    *(global_var_byte + global_var_count) = is_byte;
    global_var_count = global_var_count + 1;
}

int is_local_var(char* name) {
    int i = 0;
    while (i < local_var_count) {
        if ((str_compare(*(local_vars + i), name, str_len(name))) && (str_len(*(local_vars + i)) == str_len(name))) {
            return 1;
        }
        i = i + 1;
    }
    return 0;
}

int get_var_elem_size(char* name) {
    int i = 0;
    int nlen = str_len(name);
    while (i < local_var_count) {
        if ((str_compare(*(local_vars + i), name, nlen)) && (str_len(*(local_vars + i)) == nlen)) {
            return *(local_var_byte + i);
        }
        i = i + 1;
    }
    i = 0;
    while (i < global_var_count) {
        if ((str_compare(*(global_vars + i), name, nlen)) && (str_len(*(global_vars + i)) == nlen)) {
            return *(global_var_byte + i);
        }
        i = i + 1;
    }
    return 0;
}

// Global string literal counter and list
char** string_literals;
int string_literal_count;

char** parse_expr();

char** val_new(int kind, int val, char* name, int elem_size) {
    char** p = malloc(32);
    *p = kind;
    *(p + 1) = val;
    *(p + 2) = name;
    *(p + 3) = elem_size;
    return p;
}

char** load_val(char** v) {
    if (*v == 2) { // Variable
        int r_var = next_reg();
        if (is_local_var(*(v + 2))) {
            printf("  %%%d = load i64, i64* %%%s.alloca\n", r_var, *(v + 2));
        } else {
            printf("  %%%d = load i64, i64* @%s\n", r_var, *(v + 2));
        }
        return val_new(1, r_var, "", *(v + 3));
    }
    if (*v == 4) { // Dereferenced pointer
        int esz = *(v + 3);
        int r_ptr = next_reg();
        if (esz == 1) {
            printf("  %%%d = inttoptr i64 %%%d to i8*\n", r_ptr, *(v + 1));
            int r_byte = next_reg();
            printf("  %%%d = load i8, i8* %%%d\n", r_byte, r_ptr);
            int r_ext = next_reg();
            printf("  %%%d = zext i8 %%%d to i64\n", r_ext, r_byte);
            return val_new(1, r_ext, "", 0);
        } else {
            printf("  %%%d = inttoptr i64 %%%d to i64*\n", r_ptr, *(v + 1));
            int r_deref = next_reg();
            printf("  %%%d = load i64, i64* %%%d\n", r_deref, r_ptr);
            return val_new(1, r_deref, "", 1);
        }
    }
    return v;
}

void print_val_ref(char** v) {
    if (*v == 0) {
        if ((str_len(*(v + 2)) == 4) && (str_compare(*(v + 2), "null", 4))) {
            printf("0");
        } else {
            printf("%d", *(v + 1));
        }
    } else if (*v == 1) {
        printf("%%%d", *(v + 1));
    } else if (*v == 2) {
        if (is_local_var(*(v + 2))) {
            printf("%%%s.alloca", *(v + 2));
        } else {
            printf("@%s", *(v + 2));
        }
    } else if (*v == 3) {
        printf("%s", *(v + 2));
    }
}

// Expression parser (precedence climbing)
char** parse_primary() {
    char** ret;
    if (token_type == TOK_INT) {
        ret = val_new(0, token_val, "", 0);
        next_token();
        return ret;
    }

    if (token_type == TOK_NULL) {
        ret = val_new(0, 0, "null", 0);
        next_token();
        return ret;
    }

    if (token_type == TOK_SYMBOL && match_token("(")) {
        next_token();
        char** inner_paren = parse_expr();
        expect_symbol(")");
        return inner_paren;
    }

    if (token_type == TOK_STRING) {
        char* label = malloc(16);
        sprintf(label, "@.str.%d", string_literal_count);
        int len = str_len(token_str);
        
        // Save the string to our list
        *(string_literals + string_literal_count) = token_str;
        string_literal_count = string_literal_count + 1;

        // Get pointer gep
        int r_str = next_reg();
        printf("  %%%d = getelementptr [%d x i8], [%d x i8]* %s, i32 0, i32 0\n", r_str, len + 1, len + 1, label);
        int r_int = next_reg();
        printf("  %%%d = ptrtoint i8* %%%d to i64\n", r_int, r_str);
        
        next_token();
        return val_new(1, r_int, "", 1);
    }

    if (token_type == TOK_IDENT) {
        char* name = token_str;
        next_token();

        // Function call
        if (token_type == TOK_SYMBOL && match_token("(")) {
            next_token(); // "("
            
            // Build arguments using dynamic heap pointers
            int* arg_kinds = malloc(80);
            int* arg_vals = malloc(80);
            char** arg_names = malloc(80);
            int* arg_esizes = malloc(80);
            int arg_count = 0;
            if ((token_type != TOK_SYMBOL) || (match_token(")") == 0)) {
                char** tmp = load_val(parse_expr());
                *(arg_kinds + arg_count) = *tmp;
                *(arg_vals + arg_count) = *(tmp + 1);
                *(arg_names + arg_count) = *(tmp + 2);
                *(arg_esizes + arg_count) = *(tmp + 3);
                arg_count = arg_count + 1;
                while (token_type == TOK_SYMBOL && match_token(",")) {
                    next_token();
                    char** tmp2 = load_val(parse_expr());
                    *(arg_kinds + arg_count) = *tmp2;
                    *(arg_vals + arg_count) = *(tmp2 + 1);
                    *(arg_names + arg_count) = *(tmp2 + 2);
                    *(arg_esizes + arg_count) = *(tmp2 + 3);
                    arg_count = arg_count + 1;
                }
            }
            expect_symbol(")");

            int is_void = is_void_func(name);
            int r_call = 0;
            if (is_void) {
                printf("  call void @%s(", name);
            } else {
                r_call = next_reg();
                if ((str_compare(name, "printf", 6)) && (str_len(name) == 6)) {
                    printf("  %%%d = call i64 (i64, ...) @%s(", r_call, name);
                } else if ((str_compare(name, "sprintf", 7)) && (str_len(name) == 7)) {
                    printf("  %%%d = call i64 (i64, ...) @%s(", r_call, name);
                } else {
                    printf("  %%%d = call i64 @%s(", r_call, name);
                }
            }
            int i = 0;
            while (i < arg_count) {
                if (i > 0) { printf(", "); }
                printf("i64 "); // simplified all to i64
                char** v = val_new(*(arg_kinds + i), *(arg_vals + i), *(arg_names + i), *(arg_esizes + i));
                print_val_ref(v);
                free(v);
                i = i + 1;
            }
            printf(")\n");

            free(arg_kinds);
            free(arg_vals);
            free(arg_names);
            free(arg_esizes);

            return val_new(1, r_call, "", 0);
        }

        int var_esz = get_var_elem_size(name);
        return val_new(2, 0, name, var_esz);
    }

    if (token_type == TOK_SYMBOL && match_token("*")) { // Dereference
        next_token();
        char** inner_deref = load_val(parse_primary());
        return val_new(4, *(inner_deref + 1), "", *(inner_deref + 3));
    }

    if (token_type == TOK_SYMBOL && match_token("&")) { // Address of
        next_token();
        char** inner_addr = parse_primary();
        if (*inner_addr != 2) {
            printf("; error: Expected variable for address-of\n");
            exit(1);
        }
        int r_addr = next_reg();
        if (is_local_var(*(inner_addr + 2))) {
            printf("  %%%d = ptrtoint i64* %%%s.alloca to i64\n", r_addr, *(inner_addr + 2));
        } else {
            printf("  %%%d = ptrtoint i64* @%s to i64\n", r_addr, *(inner_addr + 2));
        }
        return val_new(1, r_addr, "", 8);
    }

    printf("; error: Unexpected primary token '%s'\n", token_str);
    exit(1);
    return ret;
}

// Simple binary expression parser
char** parse_expr() {
    int esz;
    char** left = parse_primary();

    // Check for assignment: left = right
    if (token_type == TOK_SYMBOL && match_token("=")) {
        next_token();
        char** right_assign = load_val(parse_expr());
        if (*left == 2) { // Variable assignment
            if (is_local_var(*(left + 2))) {
                printf("  store i64 ");
                print_val_ref(right_assign);
                printf(", i64* %%%s.alloca\n", *(left + 2));
            } else {
                printf("  store i64 ");
                print_val_ref(right_assign);
                printf(", i64* @%s\n", *(left + 2));
            }
        } else if (*left == 4) { // Dereferenced pointer assignment
            int store_esz = *(left + 3);
            int r_ptr = next_reg();
            if (store_esz == 1) {
                printf("  %%%d = inttoptr i64 %%%d to i8*\n", r_ptr, *(left + 1));
                int r_trunc = next_reg();
                printf("  %%%d = trunc i64 ", r_trunc);
                print_val_ref(right_assign);
                printf(" to i8\n");
                printf("  store i8 %%%d, i8* %%%d\n", r_trunc, r_ptr);
            } else {
                printf("  %%%d = inttoptr i64 %%%d to i64*\n", r_ptr, *(left + 1));
                printf("  store i64 ");
                print_val_ref(right_assign);
                printf(", i64* %%%d\n", r_ptr);
            }
        }
        return right_assign;
    }

    // Binary operations: + - * / == != < > <= >= && ||
    while (token_type == TOK_SYMBOL && (
        match_token("+") || match_token("-") ||
        match_token("*") || match_token("/") ||
        match_token("==") || match_token("!=") ||
        match_token("<=") || match_token(">=") ||
        match_token("<") || match_token(">") ||
        match_token("&&") || match_token("||")
    )) {
        char* op = token_str;
        next_token();
        char** right_op = parse_primary();

        // Load values if variable
        left = load_val(left);
        right_op = load_val(right_op);

        int r = next_reg();
        if (match_op(op, "+")) {
            esz = *(left + 3);
            if (esz > 0) {
                printf("  %%%d = mul i64 ", r);
                print_val_ref(right_op);
                printf(", %d\n", esz);
                int r_add = next_reg();
                printf("  %%%d = add i64 ", r_add);
                print_val_ref(left);
                printf(", %%%d\n", r);
                r = r_add;
            } else {
                printf("  %%%d = add i64 ", r);
                print_val_ref(left);
                printf(", ");
                print_val_ref(right_op);
                printf("\n");
            }
        } else if (match_op(op, "-")) {
            esz = *(left + 3);
            if (esz > 0) {
                printf("  %%%d = mul i64 ", r);
                print_val_ref(right_op);
                printf(", %d\n", esz);
                int r_sub = next_reg();
                printf("  %%%d = sub i64 ", r_sub);
                print_val_ref(left);
                printf(", %%%d\n", r);
                r = r_sub;
            } else {
                printf("  %%%d = sub i64 ", r);
                print_val_ref(left);
                printf(", ");
                print_val_ref(right_op);
                printf("\n");
            }
        } else if (match_op(op, "*")) {
            printf("  %%%d = mul i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
        } else if (match_op(op, "/")) {
            printf("  %%%d = sdiv i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
        } else if (match_op(op, "==")) {
            printf("  %%%d = icmp eq i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_eq = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_eq, r);
            r = r2_eq;
        } else if (match_op(op, "!=")) {
            printf("  %%%d = icmp ne i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_ne = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_ne, r);
            r = r2_ne;
        } else if (match_op(op, "<=")) {
            printf("  %%%d = icmp sle i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_le = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_le, r);
            r = r2_le;
        } else if (match_op(op, ">=")) {
            printf("  %%%d = icmp sge i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_ge = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_ge, r);
            r = r2_ge;
        } else if (match_op(op, "<")) {
            printf("  %%%d = icmp slt i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_lt = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_lt, r);
            r = r2_lt;
        } else if (match_op(op, ">")) {
            printf("  %%%d = icmp sgt i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
            int r2_gt = next_reg();
            printf("  %%%d = zext i1 %%%d to i64\n", r2_gt, r);
            r = r2_gt;
        } else if (match_op(op, "&&")) {
            printf("  %%%d = and i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
        } else if (match_op(op, "||")) {
            printf("  %%%d = or i64 ", r);
            print_val_ref(left);
            printf(", ");
            print_val_ref(right_op);
            printf("\n");
        }

        *left = 1;
        *(left + 1) = r;
        *(left + 2) = "";
    }

    return left;
}

// Statement parser
void parse_stmt();

void parse_block() {
    expect_symbol("{");
    while ((token_type != TOK_SYMBOL) || (match_token("}") == 0)) {
        parse_stmt();
    }
    expect_symbol("}");
}

void parse_stmt() {
    if (token_type == TOK_IF) {
        next_token();
        expect_symbol("(");
        char** cond_if = load_val(parse_expr());
        expect_symbol(")");

        int label_then = next_label();
        int label_else = next_label();
        int label_merge = next_label();

        int r_bool_if = next_reg();
        printf("  %%%d = icmp ne i64 ", r_bool_if);
        print_val_ref(cond_if);
        printf(", 0\n");

        printf("  br i1 %%%d, label %%if.then.%d, label %%if.else.%d\n", r_bool_if, label_then, label_else);

        // Then branch
        printf("if.then.%d:\n", label_then);
        block_terminated = 0;
        parse_stmt();
        if (block_terminated == 0) {
            printf("  br label %%if.merge.%d\n", label_merge);
        }
        int then_terminated = block_terminated;

        // Else branch
        printf("if.else.%d:\n", label_else);
        block_terminated = 0;
        if (token_type == TOK_ELSE) {
            next_token();
            parse_stmt();
        }
        if (block_terminated == 0) {
            printf("  br label %%if.merge.%d\n", label_merge);
        }
        int else_terminated = block_terminated;

        // Merge block
        printf("if.merge.%d:\n", label_merge);
        block_terminated = then_terminated && else_terminated;
        if (block_terminated) {
            printf("  unreachable\n");
        }
        return;
    }

    if (token_type == TOK_WHILE) {
        next_token();
        int label_cond = next_label();
        int label_body = next_label();
        int label_after = next_label();

        printf("  br label %%while.cond.%d\n", label_cond);
        printf("while.cond.%d:\n", label_cond);
        block_terminated = 0;

        expect_symbol("(");
        char** cond_while = load_val(parse_expr());
        expect_symbol(")");

        int r_bool_while = next_reg();
        printf("  %%%d = icmp ne i64 ", r_bool_while);
        print_val_ref(cond_while);
        printf(", 0\n");

        printf("  br i1 %%%d, label %%while.body.%d, label %%while.after.%d\n", r_bool_while, label_body, label_after);

        // Push labels to loop stacks
        *(break_stack + loop_depth) = label_after;
        *(continue_stack + loop_depth) = label_cond;
        loop_depth = loop_depth + 1;

        // Body
        printf("while.body.%d:\n", label_body);
        block_terminated = 0;
        parse_stmt();
        if (block_terminated == 0) {
            printf("  br label %%while.cond.%d\n", label_cond);
        }

        // Pop labels
        loop_depth = loop_depth - 1;

        // After
        printf("while.after.%d:\n", label_after);
        block_terminated = 0;
        return;
    }

    if (token_type == TOK_BREAK) {
        next_token();
        if (loop_depth == 0) {
            printf("; error: 'break' outside of loop\n");
            exit(1);
        }
        int b_lbl = *(break_stack + (loop_depth - 1));
        printf("  br label %%while.after.%d\n", b_lbl);
        expect_symbol(";");
        block_terminated = 1;
        return;
    }

    if (token_type == TOK_CONTINUE) {
        next_token();
        if (loop_depth == 0) {
            printf("; error: 'continue' outside of loop\n");
            exit(1);
        }
        int c_lbl = *(continue_stack + (loop_depth - 1));
        printf("  br label %%while.cond.%d\n", c_lbl);
        expect_symbol(";");
        block_terminated = 1;
        return;
    }

    if (token_type == TOK_RETURN) {
        next_token();
        if ((token_type != TOK_SYMBOL) || (match_token(";") == 0)) {
            char** val = load_val(parse_expr());
            printf("  ret i64 ");
            print_val_ref(val);
            printf("\n");
        } else {
            printf("  ret void\n");
        }
        expect_symbol(";");
        block_terminated = 1;
        return;
    }

    if ((token_type == TOK_INT_KEY) || (token_type == TOK_CHAR_KEY) || (token_type == TOK_STRUCT)) {
        // Variable declaration: type name = init;
        int is_char = (token_type == TOK_CHAR_KEY);
        int is_struct = (token_type == TOK_STRUCT);
        next_token();
        
        if (is_struct) {
            next_token(); // skip struct name (e.g. Val)
        }

        // Handle pointer stars
        int ptr_depth = 0;
        while (token_type == TOK_SYMBOL && match_token("*")) {
            next_token();
            ptr_depth = ptr_depth + 1;
        }

        if (token_type != TOK_IDENT) {
            printf("; error: Expected identifier in var declaration, got '%s'\n", token_str);
            exit(1);
        }
        char* name = token_str;
        next_token();

        // Determine element size for variable
        int decl_esz = 0;
        if (ptr_depth == 1) {
            if (is_char) { decl_esz = 1; }
            else { decl_esz = 8; }
        } else if (ptr_depth > 1) {
            decl_esz = 8;
        }
        // Local allocation - simplify to alloca i64 for all local variables
        add_local_var(name, decl_esz);
        printf("  %%%s.alloca = alloca i64\n", name);

        if (token_type == TOK_SYMBOL && match_token("=")) {
            next_token();
            char** init = load_val(parse_expr());
            printf("  store i64 ");
            print_val_ref(init);
            printf(", i64* %%%s.alloca\n", name);
        }
        expect_symbol(";");
        return;
    }

    if (token_type == TOK_SYMBOL && match_token("{")) {
        parse_block();
        return;
    }

    // Expression statement
    parse_expr();
    expect_symbol(";");
}

// Top-level declaration parser (functions, forward declarations, and global variables)
void parse_top_level() {
    int p_esz;
    int is_void = (token_type == TOK_VOID_KEY);
    int is_char = (token_type == TOK_CHAR_KEY);
    int is_struct = (token_type == TOK_STRUCT);
    next_token(); // skip type (int, char, void, or struct)

    if (is_struct) {
        if (token_type != TOK_IDENT) {
            printf("; error: Expected struct name after 'struct'\n");
            exit(1);
        }
        next_token(); // skip struct name

        if (token_type == TOK_SYMBOL && match_token("{")) {
            next_token(); // skip "{"
            while ((token_type != TOK_SYMBOL) || (match_token("}") == 0)) {
                next_token(); // skip fields
            }
            expect_symbol("}");
            return;
        }
    }

    // Handle pointer stars
    int ptr_depth_top = 0;
    while (token_type == TOK_SYMBOL && match_token("*")) {
        next_token();
        ptr_depth_top = ptr_depth_top + 1;
    }

    if (token_type != TOK_IDENT) {
        printf("; error: Expected identifier at top level, got '%s'\n", token_str);
        exit(1);
    }
    char* name = token_str;
    next_token();

    if (token_type == TOK_SYMBOL && match_token("(")) {
        // Function!
        next_token(); // "("
        local_var_count = 0;
        
        // Parse parameters
        char** params = malloc(80);
        int* param_byte = malloc(80);
        int param_count = 0;
        if ((token_type != TOK_SYMBOL) || (match_token(")") == 0)) {
            // type
            int p_is_char = (token_type == TOK_CHAR_KEY);
            if (token_type == TOK_STRUCT) {
                next_token(); // skip struct
                next_token(); // skip struct name
                p_is_char = 0;
            } else {
                next_token(); // skip primitive type
            }
            // ptr stars
            int p_depth = 0;
            while (token_type == TOK_SYMBOL && match_token("*")) { next_token(); p_depth = p_depth + 1; }
            // name
            *(params + param_count) = token_str;
            p_esz = 0;
            if (p_depth == 1) {
                if (p_is_char) { p_esz = 1; }
                else { p_esz = 8; }
            } else if (p_depth > 1) {
                p_esz = 8;
            }
            *(param_byte + param_count) = p_esz;
            param_count = param_count + 1;
            next_token();

            while (token_type == TOK_SYMBOL && match_token(",")) {
                next_token();
                // type
                p_is_char = (token_type == TOK_CHAR_KEY);
                if (token_type == TOK_STRUCT) {
                    next_token(); // skip struct
                    next_token(); // skip struct name
                    p_is_char = 0;
                } else {
                    next_token(); // skip primitive type
                }
                // ptr stars
                p_depth = 0;
                while (token_type == TOK_SYMBOL && match_token("*")) { next_token(); p_depth = p_depth + 1; }
                // name
                *(params + param_count) = token_str;
                p_esz = 0;
                if (p_depth == 1) {
                    if (p_is_char) { p_esz = 1; }
                    else { p_esz = 8; }
                } else if (p_depth > 1) {
                    p_esz = 8;
                }
                *(param_byte + param_count) = p_esz;
                param_count = param_count + 1;
                next_token();
            }
        }
        expect_symbol(")");

        if (token_type == TOK_SYMBOL && match_token(";")) {
            // Forward declaration, skip
            next_token();
            free(params);
            free(param_byte);
            return;
        }

        // LLVM function definition
        if (is_void) {
            printf("define void @%s(", name);
        } else {
            printf("define i64 @%s(", name);
        }
        int i = 0;
        while (i < param_count) {
            if (i > 0) { printf(", "); }
            printf("i64 %%%s", *(params + i));
            i = i + 1;
        }
        printf(") {\nentry:\n");

        // Allocate parameters locally
        local_var_count = 0;
        i = 0;
        while (i < param_count) {
            add_local_var(*(params + i), *(param_byte + i));
            printf("  %%%s.alloca = alloca i64\n", *(params + i));
            printf("  store i64 %%%s, i64* %%%s.alloca\n", *(params + i), *(params + i));
            i = i + 1;
        }

        // Reset register and label counters
        reg_counter = 0;
        label_counter = 0;
        block_terminated = 0;

        parse_block();

        // End function
        if (block_terminated == 0) {
            if (is_void) {
                printf("  ret void\n");
            } else {
                printf("  ret i64 0\n");
            }
        }
        printf("}\n\n");
        free(params);
        free(param_byte);
        return;
    }

    // Otherwise it is a global variable declaration!
    int g_esz = 0;
    if (ptr_depth_top == 1) {
        if (is_char) { g_esz = 1; }
        else { g_esz = 8; }
    } else if (ptr_depth_top > 1) {
        g_esz = 8;
    }
    add_global_var(name, g_esz);
    printf("@%s = internal global i64 0\n", name);

    if (token_type == TOK_SYMBOL && match_token("=")) {
        next_token();
        next_token(); // skip value (constant)
    }
    expect_symbol(";");
}

int main() {
    // Read the compiler source file itself!
    char* filename = "tests/mighty_c_compiler.mc";
    char* stream = fopen(filename, "r");
    if (stream == null) {
        printf("; error: Could not open source file %s\n", filename);
        return 1;
    }

    char* buffer = malloc(100000); // Allocate buffer for source code
    local_vars = malloc(8000);      // Allocate local variables table
    local_var_byte = malloc(8000);  // Allocate local variables byte flag table
    global_vars = malloc(8000);     // Allocate global variables table
    global_var_byte = malloc(8000); // Allocate global variables byte flag table
    global_var_count = 0;
    int bytes_read = fread(buffer, 1, 100000, stream);
    *(buffer + bytes_read) = 0; // Null terminate
    fclose(stream);

    // Initializing token values
    TOK_EOF = 0;
    TOK_IDENT = 1;
    TOK_INT = 2;
    TOK_STRING = 3;
    TOK_CHAR = 4;
    TOK_SYMBOL = 6;

    // Keywords
    TOK_INT_KEY = 10;
    TOK_CHAR_KEY = 11;
    TOK_VOID_KEY = 12;
    TOK_IF = 13;
    TOK_ELSE = 14;
    TOK_WHILE = 15;
    TOK_RETURN = 16;
    TOK_IMPORT = 17;
    TOK_EXPORT = 18;
    TOK_NULL = 19;
    TOK_STRUCT = 20;
    TOK_BREAK = 21;
    TOK_CONTINUE = 22;

    break_stack = malloc(800);
    continue_stack = malloc(800);
    loop_depth = 0;

    string_literals = malloc(16000);
    string_literal_count = 0;

    src = buffer;
    next_token();

    printf("; =========================================================================\n");
    printf("; Textual LLVM IR generated by the bootstrapped Mighty C compiler\n");
    printf("; =========================================================================\n\n");

    // External library prototypes required for LLVM linkage
    printf("declare i64 @printf(i64, ...)\n");
    printf("declare i64 @sprintf(i64, i64, ...)\n");
    printf("declare i64 @fopen(i64, i64)\n");
    printf("declare i64 @fread(i64, i64, i64, i64)\n");
    printf("declare i64 @fclose(i64)\n");
    printf("declare i64 @fflush(i64)\n");
    printf("declare i64 @malloc(i64)\n");
    printf("declare void @free(i64)\n");
    printf("declare void @exit(i64)\n\n");

    // Loop through top level declarations
    while (token_type != TOK_EOF) {
        if (token_type == TOK_IMPORT) {
            next_token();
            next_token(); // skip module name
            expect_symbol(";");
        } else if (token_type == TOK_EXPORT) {
            next_token();
            parse_top_level();
        } else if ((token_type == TOK_INT_KEY) || (token_type == TOK_CHAR_KEY) || (token_type == TOK_VOID_KEY) || (token_type == TOK_STRUCT)) {
            parse_top_level();
        } else {
            // Unhandled top-level element, skip it
            next_token();
        }
    }

    // Now output all string literal definitions at the global scope!
    int i = 0;
    while (i < string_literal_count) {
        char* s = *(string_literals + i);
        int len = str_len(s);
        printf("@.str.%d = internal constant [%d x i8] c", i, len + 1);
        printf("%c", 34);
        int j = 0;
        while (j < len) {
            print_hex_byte(*(s + j));
            j = j + 1;
        }
        printf("\\00");
        printf("%c\n", 34);
        i = i + 1;
    }

    free(buffer);
    free(local_vars);
    free(local_var_byte);
    free(global_vars);
    free(global_var_byte);
    free(string_literals);
    return 0;
}
