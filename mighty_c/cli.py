"""
mighty_c/cli.py — Command-Line Interface for the Mighty C Compiler
===================================================================

Uses ``typer`` for the CLI framework and ``rich`` for colored diagnostics.

Commands:
    compile <file.mc> [--output name] [--emit-llvm]

Pipeline:
    Read source → Parse (Lark) → Semantic analysis → LLVM IR codegen
    → Write .ll file → (optionally) invoke clang → native binary
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from mighty_c import __version__

console = Console(stderr=True)
app = typer.Typer(
    name="mighty-c",
    help="🦾 Mighty C — A modern, safe C-dialect compiler targeting LLVM.",
    add_completion=False,
)


def _print_banner() -> None:
    """Print a styled compiler banner."""
    banner = Text()
    banner.append("⚡ Mighty C Compiler ", style="bold cyan")
    banner.append(f"v{__version__}", style="dim")
    console.print(Panel(banner, border_style="cyan", expand=False))


def _print_errors(errors: list, source: str, filepath: str) -> None:
    """Print semantic/parse errors with Rich formatting."""
    lines = source.splitlines()
    for err in errors:
        # Error header
        loc = ""
        if err.line is not None:
            loc = f"{filepath}:{err.line}"
            if err.col is not None:
                loc += f":{err.col}"
            loc += " "

        console.print(f"[bold red]error:[/bold red] {loc}{err.message}")

        # Show the offending source line
        if err.line is not None and 1 <= err.line <= len(lines):
            line_text = lines[err.line - 1]
            console.print(f"  [dim]{err.line:4d} │[/dim] {line_text}")
            if err.col is not None:
                pointer = " " * (7 + err.col - 1) + "^"
                console.print(f"[bold red]{pointer}[/bold red]")
        console.print()


@app.command()
def compile(
    input_file: str = typer.Argument(
        ..., help="Path to the .mc source file to compile."
    ),
    output: str = typer.Option(
        None, "--output", "-o",
        help="Output binary name (default: input filename without extension)."
    ),
    emit_llvm: bool = typer.Option(
        False, "--emit-llvm",
        help="Emit LLVM IR (.ll file) instead of compiling to a binary."
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Show verbose output including the generated LLVM IR."
    ),
) -> None:
    """Compile a Mighty C source file (.mc) to a native binary or LLVM IR."""
    _print_banner()

    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[bold red]error:[/bold red] File not found: {input_file}")
        raise typer.Exit(code=1)

    if not input_path.suffix == ".mc":
        console.print(
            f"[bold yellow]warning:[/bold yellow] "
            f"Expected .mc extension, got '{input_path.suffix}'"
        )

    # Determine output name
    if output is None:
        output = input_path.stem

    source = input_path.read_text(encoding="utf-8")
    console.print(f"[dim]→ Reading[/dim] {input_file} [dim]({len(source)} bytes)[/dim]")

    # ── Step 1: Parse ──────────────────────────────────────────────────
    console.print("[dim]→ Parsing...[/dim]")
    try:
        from mighty_c.parser import parse_source
        program = parse_source(source)
    except Exception as e:
        console.print(f"[bold red]error:[/bold red] Parse error: {e}")
        raise typer.Exit(code=1)

    console.print(
        f"  [green]✓[/green] Parsed {len(program.declarations)} top-level declarations"
    )

    # ── Step 2: Semantic Analysis ──────────────────────────────────────
    console.print("[dim]→ Analyzing semantics...[/dim]")
    from mighty_c.semantic import analyze
    result = analyze(program)

    if not result.ok:
        _print_errors(result.errors, source, input_file)
        console.print(
            f"[bold red]✗ {len(result.errors)} error(s) found. Compilation aborted.[/bold red]"
        )
        raise typer.Exit(code=1)

    console.print("  [green]✓[/green] Semantic analysis passed")

    # ── Step 3: LLVM IR Code Generation ───────────────────────────────
    console.print("[dim]→ Generating LLVM IR...[/dim]")
    from mighty_c.codegen import generate
    llvm_module = generate(program, module_name=input_path.stem)

    ir_text = str(llvm_module)
    console.print(f"  [green]✓[/green] Generated {len(ir_text)} bytes of LLVM IR")

    if verbose:
        console.print()
        console.print(
            Syntax(ir_text, "llvm", theme="monokai", line_numbers=True)
        )
        console.print()

    # ── Step 4: Write .ll file ────────────────────────────────────────
    ll_path = Path(f"{output}.ll")
    ll_path.write_text(ir_text, encoding="utf-8")
    console.print(f"  [green]✓[/green] Wrote {ll_path}")

    if emit_llvm:
        console.print(
            Panel(
                f"[bold green]✓ LLVM IR written to {ll_path}[/bold green]",
                border_style="green",
                expand=False,
            )
        )
        return

    # ── Step 5: Compile with clang ────────────────────────────────────
    console.print("[dim]→ Compiling with clang...[/dim]")

    # Determine the output binary name
    binary_ext = ".exe" if sys.platform == "win32" else ""
    binary_path = f"{output}{binary_ext}"

    # Search for portable clang toolchain in .tools/llvm-mingw/bin/
    workspace_root = Path(__file__).resolve().parents[1]
    local_clang = workspace_root / ".tools" / "llvm-mingw" / "bin" / ("clang.exe" if sys.platform == "win32" else "clang")
    
    if local_clang.exists():
        clang_bin = str(local_clang)
        console.print(f"  [dim]Using portable toolchain: {clang_bin}[/dim]")
    else:
        clang_bin = "clang"

    cmd = [clang_bin, str(ll_path), "-o", binary_path, "-lm"]
    
    # Configure project-local temp directory for clang builds
    temp_dir = workspace_root / ".tools" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    env = dict(os.environ)
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if proc.returncode != 0:
            console.print(f"[bold red]error:[/bold red] clang failed:")
            console.print(proc.stderr)
            raise typer.Exit(code=1)
    except FileNotFoundError:
        if clang_bin == "clang":
            console.print(
                "[bold yellow]warning:[/bold yellow] "
                "'clang' not found on PATH. LLVM IR was written to "
                f"[cyan]{ll_path}[/cyan] — you can compile it manually:\n"
                f"  [dim]clang {ll_path} -o {binary_path}[/dim]\n"
                "Or run 'python bootstrap_env.py' to download a portable toolchain."
            )
        else:
            console.print(f"[bold red]error:[/bold red] Clang executable not found at {clang_bin}")
        raise typer.Exit(code=0)
    except subprocess.TimeoutExpired:
        console.print("[bold red]error:[/bold red] clang timed out after 30s")
        raise typer.Exit(code=1)

    console.print(f"  [green]✓[/green] Compiled to {binary_path}")
    console.print(
        Panel(
            f"[bold green]✓ Build successful![/bold green]\n"
            f"  Binary: [cyan]{binary_path}[/cyan]\n"
            f"  LLVM IR: [cyan]{ll_path}[/cyan]",
            border_style="green",
            expand=False,
        )
    )


@app.command()
def version() -> None:
    """Print the compiler version."""
    console.print(f"Mighty C Compiler v{__version__}")


@app.command()
def dump_ast(
    input_file: str = typer.Argument(
        ..., help="Path to the .mc source file."
    ),
) -> None:
    """Parse a .mc file and print the AST as JSON."""
    input_path = Path(input_file)
    if not input_path.exists():
        console.print(f"[bold red]error:[/bold red] File not found: {input_file}")
        raise typer.Exit(code=1)

    source = input_path.read_text(encoding="utf-8")

    try:
        from mighty_c.parser import parse_source
        program = parse_source(source)
    except Exception as e:
        console.print(f"[bold red]error:[/bold red] Parse error: {e}")
        raise typer.Exit(code=1)

    # Pretty-print the AST as JSON
    import json
    console.print(
        Syntax(
            program.model_dump_json(indent=2),
            "json",
            theme="monokai",
            line_numbers=True,
        )
    )
