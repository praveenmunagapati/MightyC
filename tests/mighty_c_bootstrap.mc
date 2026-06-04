import io;

// Simple string equality helper
int str_equals(char* a, char* b, int len) {
    for (int i = 0; i < len; i = i + 1) {
        if (a[i] != b[i]) {
            return 0;
        }
    }
    return 1;
}

// Simple digit checking helper
int is_digit(char c) {
    return c >= 48 && c <= 57; // '0' is 48, '9' is 57 in ASCII
}

int main() {
    // Input source code string representing a minimal Mighty C program to compile.
    char* source = "int main() { return 42; }";

    printf("; --- Mighty C Bootstrapped Compiler (Stage 1) ---\n");
    printf("; Input Source: %s\n\n", source);

    char* p = source;

    // Skip spaces
    while (*p == 32) { // ASCII 32 is space
        p = p + 1;
    }

    // Check header "int main() {"
    // len("int main() {") is 12
    if (str_equals(p, "int main() {", 12) == 0) {
        printf("; error: Expected 'int main() {'\n");
        return 1;
    }
    p = p + 12;

    // Skip spaces
    while (*p == 32) {
        p = p + 1;
    }

    // Check "return"
    // len("return") is 6
    if (str_equals(p, "return", 6) == 0) {
        printf("; error: Expected 'return'\n");
        return 1;
    }
    p = p + 6;

    // Must have a space after "return" before the digit
    if (*p != 32) {
        printf("; error: Expected space after 'return'\n");
        return 1;
    }
    while (*p == 32) {
        p = p + 1;
    }

    // Parse the integer literal
    int value = 0;
    int has_digits = 0;
    while (is_digit(*p)) {
        value = value * 10 + (*p - 48);
        p = p + 1;
        has_digits = 1;
    }

    if (has_digits == 0) {
        printf("; error: Expected integer literal\n");
        return 1;
    }

    // Skip spaces
    while (*p == 32) {
        p = p + 1;
    }

    // Check ";" (ASCII 59)
    if (*p != 59) {
        printf("; error: Expected ';'\n");
        return 1;
    }
    p = p + 1;

    // Skip spaces
    while (*p == 32) {
        p = p + 1;
    }

    // Check "}" (ASCII 125)
    if (*p != 125) {
        printf("; error: Expected '}'\n");
        return 1;
    }

    // Codegen: Emit valid LLVM IR
    printf("define i32 @main() {\n");
    printf("entry:\n");
    printf("  ret i32 %d\n", value);
    printf("}\n");

    return 0;
}
