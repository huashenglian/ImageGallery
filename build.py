from __future__ import annotations

import os
import shutil
import subprocess
import sys


PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
ICON_PATH = os.path.join(PROJECT_ROOT, "icon.ico")

DATA_DIRS = ["cache", "model_cache", "thumbnails", "logs"]


def _check_tool(name: str) -> bool:
    try:
        import importlib.util
        spec = importlib.util.find_spec(name)
        return spec is not None
    except (ModuleNotFoundError, ValueError):
        return False


def _check_nuitka() -> bool:
    if not _check_tool("nuitka"):
        return False
    try:
        result = subprocess.run(
            [sys.executable, "-m", "nuitka", "--version"],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"Nuitka version: {result.stdout.strip().splitlines()[0]}")
        else:
            print("Nuitka found (version check timed out)")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("Nuitka found (version check timed out)")
    return True


def _check_msvc() -> bool:
    try:
        vswhere = os.path.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Microsoft Visual Studio", "Installer", "vswhere.exe",
        )
        if not os.path.isfile(vswhere):
            return False
        result = subprocess.run(
            [vswhere, "-latest", "-property", "installationPath"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"MSVC found: {result.stdout.strip()}")
            return True
    except Exception:
        pass
    return False


def _check_icon() -> tuple[list[str], list[str]]:
    if os.path.isfile(ICON_PATH):
        print(f"Icon found: {ICON_PATH}")
        nuitka_icon = [f"--windows-icon-from-ico={ICON_PATH}"]
        pyinstaller_icon = ["-i", ICON_PATH]
        return nuitka_icon, pyinstaller_icon
    print("WARNING: icon.ico not found, building without icon")
    return [], []


def _clean_dist() -> None:
    if os.path.isdir(DIST_DIR):
        print(f"Cleaning old dist: {DIST_DIR}")
        shutil.rmtree(DIST_DIR, ignore_errors=True)


def _build_nuitka(icon_args: list[str]) -> bool:
    cmd = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--enable-plugin=pyside6",
        "--lto=yes",
        "--assume-yes-for-downloads",
        "--output-dir=dist",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=test",
        "--nofollow-import-to=distutils",
        "--nofollow-import-to=setuptools",
        "--nofollow-import-to=pip",
        "--nofollow-import-to=xmlrpc",
        "--nofollow-import-to=pydoc",
    ]

    py_ver = sys.version_info[:2]
    if py_ver >= (3, 13):
        if _check_msvc():
            cmd.append("--msvc=latest")
            print("Using MSVC backend (Python 3.13+)")
        else:
            print("WARNING: MSVC not found, using default backend")
            print("  If build fails, install Visual Studio Build Tools:")
            print("  https://visualstudio.microsoft.com/visual-cpp-build-tools/")
    else:
        cmd.append("--mingw64")
        print("Using MinGW64 backend (Python < 3.13)")

    cmd += [
        "--windows-company-name=ImageGallery",
        "--windows-product-name=ImageGallery",
        "--windows-file-version=0.14.0",
        "--windows-file-description=ImageGallery",
    ]
    cmd += icon_args
    cmd.append("main.py")

    print("\nStarting Nuitka build...")
    print(f"Command: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"\nERROR: Nuitka build failed with exit code {result.returncode}")
        return False
    return True


def _build_pyinstaller(icon_args: list[str]) -> bool:
    if not _check_tool("PyInstaller"):
        print("Installing PyInstaller...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "pyinstaller"],
            check=True,
        )

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--windowed",
        "--name=ImageGallery",
        "--exclude-module=tkinter",
        "--exclude-module=unittest",
        "--exclude-module=test",
        "--exclude-module=distutils",
        "--exclude-module=setuptools",
        "--exclude-module=pip",
        "--exclude-module=xmlrpc",
        "--exclude-module=pydoc",
    ]
    cmd += icon_args
    cmd.append("main.py")

    print("\nStarting PyInstaller build (fallback)...")
    print(f"Command: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"\nERROR: PyInstaller build failed with exit code {result.returncode}")
        return False
    return True


def _post_build_nuitka() -> None:
    app_dir = os.path.join(DIST_DIR, "main.dist")
    if not os.path.isdir(app_dir):
        print(f"ERROR: Expected output directory not found: {app_dir}")
        sys.exit(1)

    for d in DATA_DIRS:
        target = os.path.join(app_dir, d)
        os.makedirs(target, exist_ok=True)
        print(f"Created data directory: {target}")

    presets_src = os.path.join(PROJECT_ROOT, "model_cache", "presets")
    presets_dst = os.path.join(app_dir, "model_cache", "presets")
    if os.path.isdir(presets_src):
        if not os.path.isdir(presets_dst):
            shutil.copytree(presets_src, presets_dst)
            print(f"Copied presets: {presets_dst}")

    _print_build_info(app_dir, "main.exe")


def _post_build_pyinstaller() -> None:
    app_dir = os.path.join(DIST_DIR)
    exe_path = os.path.join(app_dir, "ImageGallery.exe")
    if not os.path.isfile(exe_path):
        exe_path = os.path.join(DIST_DIR, "main.exe")

    for d in DATA_DIRS:
        target = os.path.join(DIST_DIR, d)
        os.makedirs(target, exist_ok=True)
        print(f"Created data directory: {target}")

    presets_src = os.path.join(PROJECT_ROOT, "model_cache", "presets")
    presets_dst = os.path.join(DIST_DIR, "model_cache", "presets")
    if os.path.isdir(presets_src):
        if not os.path.isdir(presets_dst):
            shutil.copytree(presets_src, presets_dst)
            print(f"Copied presets: {presets_dst}")

    _print_build_info(DIST_DIR, os.path.basename(exe_path))


def _print_build_info(app_dir: str, exe_name: str) -> None:
    exe_path = os.path.join(app_dir, exe_name)
    if os.path.isfile(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"\nBuild successful!")
        print(f"Executable: {exe_path}")
        print(f"EXE size: {size_mb:.1f} MB")

        total_size = 0
        for dirpath, _, filenames in os.walk(app_dir):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        print(f"Total distribution size: {total_size / (1024 * 1024):.1f} MB")
    else:
        print(f"WARNING: {exe_name} not found in {app_dir}")


def main() -> None:
    print("=" * 60)
    print("  ImageGallery - Build Script")
    print("=" * 60)
    print()

    nuitka_icon, pyinstaller_icon = _check_icon()
    _clean_dist()

    if _check_nuitka():
        print()
        if _build_nuitka(nuitka_icon):
            _post_build_nuitka()
            return
        print("\nNuitka build failed, falling back to PyInstaller...\n")

    if _build_pyinstaller(pyinstaller_icon):
        _post_build_pyinstaller()
    else:
        print("\nAll build methods failed.")
        sys.exit(1)


if __name__ == "__main__":
    main()
