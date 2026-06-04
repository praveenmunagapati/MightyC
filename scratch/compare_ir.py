import re
import sys

def parse_ll(filename):
    with open(filename, 'r') as f:
        content = f.read()
    
    # Extract functions
    # Function start: define [internal] [type] @"?name"? ( [args] ) {
    # Function end: }
    pattern = re.compile(r'define\s+[^@\n]+@"?(\w+)"?\s*\([^)]*\)\s*(?:#[0-9]+)?\s*\{([^}]*)\}', re.DOTALL)
    functions = {}
    for match in pattern.finditer(content):
        func_name = match.group(1)
        func_body = match.group(2)
        lines = []
        for line in func_body.split('\n'):
            line = line.strip()
            if not line or line.startswith(';'):
                continue
            # Remove LLVM register names like %1, %2 to compare structural instructions
            line_norm = re.sub(r'%\d+', '%REG', line)
            line_norm = re.sub(r'%"?[a-zA-Z_]\w*(?:\.\w+)*"?', '%REG', line_norm)
            lines.append(line_norm)
        functions[func_name] = lines
    return functions

def main():
    s1 = parse_ll('mighty_c_compiler_s2.ll')
    s2 = parse_ll('mighty_c_compiler_s3.ll')
    
    print(f"S1 functions: {len(s1)}")
    print(f"S2 functions: {len(s2)}")
    
    all_funcs = set(s1.keys()).union(set(s2.keys()))
    for func in sorted(all_funcs):
        if func not in s1:
            print(f"Function {func} only in S2!")
            continue
        if func not in s2:
            print(f"Function {func} only in S1!")
            continue
        
        body1 = s1[func]
        body2 = s2[func]
        if body1 != body2:
            print(f"\n--- Difference in function: {func} ---")
            print(f"S1 instruction count: {len(body1)}")
            print(f"S2 instruction count: {len(body2)}")
            # Show diff
            max_len = max(len(body1), len(body2))
            diff_count = 0
            for i in range(max_len):
                l1 = body1[i] if i < len(body1) else "<EOF>"
                l2 = body2[i] if i < len(body2) else "<EOF>"
                if l1 != l2:
                    print(f"Line {i}:")
                    print(f"  S1: {l1}")
                    print(f"  S2: {l2}")
                    diff_count += 1
                    if diff_count >= 10:
                        print("... truncated diff ...")
                        break

if __name__ == '__main__':
    main()
