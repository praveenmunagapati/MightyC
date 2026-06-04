import sys
from pathlib import Path

# Add project root to path for local imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mighty_c.parser import parse_file
from mighty_c.semantic import analyze
from mighty_c.codegen import generate

import llvmlite.binding as llvm
# Parse, analyze, and generate
test_dir = Path(__file__).resolve().parent
ast = parse_file(str(test_dir / "test.mc"))
result = analyze(ast)
assert result.ok, f"Semantic errors: {result.errors}"

module = generate(ast)
ir_text = str(module)

# Parse the IR text with llvmlite
try:
    mod = llvm.parse_assembly(ir_text)
    mod.verify()
    print("LLVM IR verification: PASSED")
    print(f"Module has {len(list(mod.functions))} function(s)")
    for fn in mod.functions:
        print(f"  - {fn.name} (is_declaration={fn.is_declaration})")
except Exception as e:
    print(f"LLVM IR verification FAILED: {e}")
    # Print the IR for debugging
    print("\n--- Generated IR ---")
    for i, line in enumerate(ir_text.splitlines(), 1):
        print(f"{i:4d}: {line}")
