"""
bootstrap_env.py — Mighty C Portable Development Environment Bootstrapper
========================================================================

Automates the creation of a local virtual environment (.venv), installs all
compiler dependencies, downloads and extracts a portable LLVM/Clang toolchain
(llvm-mingw) for Windows, and sets up project wrappers.

No external dependencies are required to run this script.
"""

import os
import sys
import subprocess
import shutil
import urllib.request
import zipfile
from pathlib import Path

# URL for a stable ucrt x86_64 release of llvm-mingw (approx. 120MB)
LLVM_MINGW_URL = "https://github.com/mstorsjo/llvm-mingw/releases/download/20240619/llvm-mingw-20240619-ucrt-x86_64.zip"
LLVM_MINGW_DIR_NAME = "llvm-mingw-20240619-ucrt-x86_64"


def log(msg: str):
    print(f"[*] {msg}")


def check_python_version():
    log(f"Running on Python {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        print("[!] Error: Mighty C requires Python 3.10 or newer.")
        sys.exit(1)


def create_virtual_env(root: Path) -> tuple[Path, Path]:
    venv_dir = root / ".venv"
    if not venv_dir.exists():
        log("Creating virtual environment in .venv...")
        import venv
        venv.create(venv_dir, with_pip=True)
    else:
        log("Virtual environment (.venv) already exists.")

    if sys.platform == "win32":
        python_exe = venv_dir / "Scripts" / "python.exe"
        pip_exe = venv_dir / "Scripts" / "pip.exe"
    else:
        python_exe = venv_dir / "bin" / "python"
        pip_exe = venv_dir / "bin" / "pip"

    return python_exe, pip_exe


def install_dependencies(pip_exe: Path, root: Path):
    log("Installing dependencies in editable mode (isolated, no-cache)...")
    temp_dir = root / ".tools" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    # Isolate environment variables for pip
    env = dict(os.environ)
    env["TEMP"] = str(temp_dir)
    env["TMP"] = str(temp_dir)
    
    try:
        subprocess.run([
            str(pip_exe),
            "install",
            "--isolated",
            "--no-cache-dir",
            "-e",
            "."
        ], env=env, check=True)
        log("Dependencies installed successfully.")
    except subprocess.CalledProcessError as e:
        print(f"[!] Pip installation failed: {e}")
        sys.exit(1)


def download_progress_hook(count, block_size, total_size):
    """Callback for urllib to display a progress bar."""
    percent = int(count * block_size * 100 / total_size)
    percent = min(100, percent)
    downloaded = count * block_size / (1024 * 1024)
    total = total_size / (1024 * 1024)
    sys.stdout.write(f"\r    Downloading: {percent}% [{downloaded:.1f} MB / {total:.1f} MB]")
    sys.stdout.flush()


def setup_portable_clang(root: Path):
    # Only download/extract on Windows since this is a Windows environment task,
    # or if clang is not found on PATH.
    tools_dir = root / ".tools"
    target_dir = tools_dir / "llvm-mingw"

    # Check if already installed
    clang_path = target_dir / "bin" / "clang.exe"
    if clang_path.exists():
        log(f"Portable Clang already present at: {clang_path}")
        return

    # Check if system clang is available
    if shutil.which("clang"):
        log("System Clang detected on PATH. Portable toolchain download skipped.")
        return

    if sys.platform != "win32":
        log("Non-Windows platform: Skipping automatic portable clang download.")
        log("Please install 'clang' using your system package manager.")
        return

    log("Clang not detected on system PATH. Setting up portable llvm-mingw toolchain...")
    tools_dir.mkdir(exist_ok=True)
    zip_path = tools_dir / "llvm-mingw.zip"

    # Download zip file
    log(f"Downloading from {LLVM_MINGW_URL}...")
    try:
        urllib.request.urlretrieve(LLVM_MINGW_URL, zip_path, reporthook=download_progress_hook)
        print()  # Newline after progress bar
        log("Download completed.")
    except Exception as e:
        print(f"\n[!] Failed to download llvm-mingw: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return

    # Extract zip file
    log("Extracting archive...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            # We want to extract it inside .tools/
            zip_ref.extractall(tools_dir)
        log("Extraction completed.")
    except Exception as e:
        print(f"[!] Extraction failed: {e}")
        return
    finally:
        if zip_path.exists():
            zip_path.unlink()  # Clean up zip file

    # Rename extracted directory to 'llvm-mingw'
    extracted_dir = tools_dir / LLVM_MINGW_DIR_NAME
    if extracted_dir.exists():
        if target_dir.exists():
            shutil.rmtree(target_dir)
        extracted_dir.rename(target_dir)
        log(f"Portable toolchain set up at: {target_dir}")
    else:
        print(f"[!] Warning: Expected folder {extracted_dir} not found after extraction.")


def create_runners(root: Path):
    log("Creating compiler execution wrappers...")
    
    # Windows batch runner
    bat_path = root / "mighty-c.bat"
    bat_content = (
        "@echo off\n"
        '"%~dp0.venv\\Scripts\\python.exe" -m mighty_c %*\n'
    )
    bat_path.write_text(bat_content, encoding="utf-8")
    log("Created Windows runner: mighty-c.bat")

    # Unix shell runner
    sh_path = root / "mighty-c"
    sh_content = (
        "#!/bin/bash\n"
        'DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"\n'
        '"$DIR/.venv/bin/python" -m mighty_c "$@"\n'
    )
    sh_path.write_text(sh_content, encoding="utf-8")
    # Make executable on Unix
    try:
        st = os.stat(sh_path)
        os.chmod(sh_path, st.st_mode | 0o111)
    except Exception:
        pass
    log("Created Unix runner: mighty-c")


def main():
    root = Path(__file__).resolve().parent
    print("====================================================")
    print("Setting up Mighty C Portable Development Env")
    print("====================================================")
    
    check_python_version()
    python_exe, pip_exe = create_virtual_env(root)
    install_dependencies(pip_exe, root)
    setup_portable_clang(root)
    create_runners(root)
    
    print("====================================================")
    print("Environment Setup Complete!")
    print("====================================================")
    print("To compile a Mighty C file:")
    print("  ./mighty-c.bat compile tests/test.mc")
    print("====================================================")


if __name__ == "__main__":
    main()
