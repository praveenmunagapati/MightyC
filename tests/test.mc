// ============================================================
// test.mc — Mighty C Test Program
// ============================================================
// Exercises: import/export, struct, enum, for loops, switch,
// pointers, defer, while, do-while, function calls, recursion.
// ============================================================

import io;

// ── Struct definition ──────────────────────────────────────────
struct Point {
    int x;
    int y;
}

// ── Enum definition (scoped) ───────────────────────────────────
enum Color {
    RED,
    GREEN,
    BLUE
}

// ── Exported function ──────────────────────────────────────────
export int add(int a, int b) {
    return a + b;
}

// ── Recursive function ─────────────────────────────────────────
int factorial(int n) {
    if (n <= 1) {
        return 1;
    }
    return n * factorial(n - 1);
}

// ── Main entry point ───────────────────────────────────────────
int main() {
    // --- Variables and arithmetic ---
    int result = add(3, 4);
    printf("add(3, 4) = %d\n", result);

    // --- Struct usage ---
    struct Point p;
    p.x = 10;
    p.y = 20;
    printf("Point: (%d, %d)\n", p.x, p.y);

    // --- Enum usage (scoped) ---
    int color = Color.RED;
    printf("Color.RED = %d\n", color);

    // --- For loop ---
    int sum = 0;
    for (int i = 0; i < 10; i = i + 1) {
        sum = sum + i;
    }
    printf("Sum 0..9 = %d\n", sum);

    // --- While loop ---
    int count = 5;
    while (count > 0) {
        count = count - 1;
    }
    printf("Count after while: %d\n", count);

    // --- Do-while loop ---
    int dw = 0;
    do {
        dw = dw + 1;
    } while (dw < 3);
    printf("Do-while result: %d\n", dw);

    // --- Switch statement ---
    switch (color) {
        case 0:
            result = 100;
            break;
        case 1:
            result = 200;
            break;
        default:
            result = 300;
            break;
    }
    printf("Switch result: %d\n", result);

    // --- Pointer usage ---
    int val = 42;
    int* ptr = &val;
    printf("Pointer deref: %d\n", *ptr);

    // --- Defer (prints last, after other statements) ---
    defer printf("Deferred: Goodbye!\n");

    // --- Recursive function ---
    int fact = factorial(5);
    printf("factorial(5) = %d\n", fact);

    // --- Boolean and comparison ---
    bool flag = true;
    if (flag == true) {
        printf("Boolean works!\n");
    }

    printf("All tests passed!\n");

    return 0;
}
