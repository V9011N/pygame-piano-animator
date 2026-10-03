"""
build.py - Build Piano Animator as a single executable with Nuitka.

    pip install -r requirements.txt -r requirements-build.txt
    python build.py              # -> dist/PianoAnimator.exe (Windows; no console window)
    python build.py --console    # keep a console window, to see print() output while testing

Windows needs a C compiler: Nuitka offers to download MinGW64 the first time
(answered yes here with --assume-yes-for-downloads), or uses Visual Studio's
if installed. The first build takes several minutes; later ones reuse the
compiled parts in build/.

The program unpacks itself once per version to the user's cache folder
({CACHE_DIR}/PianoAnimator/<version>), so it starts quickly after the first
run. The user's pianists, settings and log are kept apart from it - see
paths.py.
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from version import VERSION  # noqa: E402

NAME = "PianoAnimator"


def numeric_version():
    """'v26.1.10' -> '26.1.10.0' (Windows file versions are four numbers)."""
    parts = [int(p) for p in VERSION.lstrip("v").split(".") if p.isdigit()][:4]
    return ".".join(str(p) for p in parts + [0] * (4 - len(parts)))


def have_tkinter():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def command(console=False):
    windows = sys.platform == "win32"
    exe = NAME + (".exe" if windows else ".bin")
    cmd = [
        sys.executable, "-m", "nuitka", "main.py",
        "--onefile",
        f"--output-dir={os.path.join(ROOT, 'build')}",
        f"--output-filename={exe}",
        "--onefile-tempdir-spec={CACHE_DIR}/PianoAnimator/{VERSION}",
        "--include-data-files=CHANGELOG.md=CHANGELOG.md",              # the menu's "What's new"
        "--include-data-files=assets/icon.png=assets/icon.png",        # the window icon
        # tools and tests that the app never imports
        "--nofollow-import-to=pig_eval,learn_weights,tests,conftest,pytest",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-data-files=pretty_midi/*.sf2",                   # a soundfont for fluidsynth, unused
        "--assume-yes-for-downloads",
        "--product-name=Piano Animator",
        f"--product-version={numeric_version()}",
        f"--file-version={numeric_version()}",
        "--file-description=Piano Animator - falling notes, animated hands and a fingering editor",
        "--company-name=Piano Animator",
        "--copyright=Piano Animator",
    ]
    if have_tkinter():
        cmd.append("--enable-plugin=tk-inter")                         # the file dialogs
    if windows:
        cmd += [f"--windows-console-mode={'force' if console else 'disable'}",
                "--windows-icon-from-ico=assets/icon.ico"]
    return cmd, exe


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--console", action="store_true", help="keep a console window (Windows)")
    ap.add_argument("--clean", action="store_true", help="start from scratch (delete build/ first)")
    args = ap.parse_args()
    try:
        import nuitka  # noqa: F401
    except ImportError:
        sys.exit("Nuitka isn't installed: pip install -r requirements-build.txt")
    if args.clean:
        shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)
    if not have_tkinter():
        print("Warning: this Python has no tkinter - the built program's file dialogs won't open "
              "(files can still be dropped on its window)")
    cmd, exe = command(args.console)
    print("Building Piano Animator", VERSION)
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", exe)
    shutil.move(os.path.join(ROOT, "build", exe), out)
    print(f"\nDone: {out} ({os.path.getsize(out) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
