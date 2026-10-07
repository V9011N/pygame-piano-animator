"""
build.py - Build Hand-thesia as a single executable with Nuitka.

    pip install -r requirements.txt -r requirements-build.txt
    python build.py              # -> dist/Hand-thesia.exe (Windows; no console window)
    python build.py --console    # keep a console window, to see print() output while testing

Windows needs a C compiler: Nuitka offers to download MinGW64 the first time
(answered yes here with --assume-yes-for-downloads), or uses Visual Studio's
if installed. The first build takes several minutes; later ones reuse the
compiled parts in build/.

The program unpacks itself once per version to the user's cache folder
({CACHE_DIR}/Hand-thesia/<version>), so it starts quickly after the first
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

NAME = "Hand-thesia"


def numeric_version(version=VERSION):
    """
    'v26.1.10' -> '26.1.10.0', 'v26.1.18.SNAPSHOT-01' -> '26.1.18.1' (Windows
    file versions are four numbers: a snapshot's number is the fourth).
    """
    parts = []
    for p in version.lstrip("v").split("."):
        if p.isdigit():
            parts.append(int(p))
        elif p.upper().startswith("SNAPSHOT-") and p[9:].isdigit():
            parts.append(int(p[9:]))
    parts = parts[:4]
    return ".".join(str(p) for p in parts + [0] * (4 - len(parts)))


def have_tkinter():
    try:
        import tkinter  # noqa: F401
        return True
    except ImportError:
        return False


def have_module(name):
    import importlib.util
    return importlib.util.find_spec(name) is not None


def _real_library(interp, path, dest):
    """
    A Tcl/Tk script library as a folder: `path` itself, or - built into the
    Tcl/Tk DLL, as Tcl/Tk 9 does (`//zipfs:/...`, e.g. the python.org 3.14
    installer) - a copy of it made at `dest` with Tcl's own `file copy`.
    """
    if not path.startswith("//zipfs:"):
        return path
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    interp.eval("file copy -force {%s} {%s}" % (path, dest.replace(os.sep, "/")))
    return dest


def probe_tcl_tk(out_dir):
    """Print TCL=<dir> and TK=<dir> for this Python's tkinter (run in a subprocess: Tk opens a hidden window)."""
    import tkinter
    tcl = tkinter.Tcl()
    print("TCL=" + _real_library(tcl, tcl.eval("info library"), os.path.join(out_dir, "tcl")))
    try:
        root = tkinter.Tk()
        root.withdraw()
        print("TK=" + _real_library(root, root.eval("set tk_library"), os.path.join(out_dir, "tk")))
        root.destroy()
    except tkinter.TclError as exc:                       # (no display, on a server)
        print(f"TKERR={exc}")


def tcl_tk_options():
    """
    --tcl-library-dir / --tk-library-dir for Nuitka's tk-inter plugin, from
    where this Python's Tcl and Tk say their libraries are. The plugin only
    looks in the usual folders, and Tcl/Tk 9 keeps its library inside its DLL
    ("Could not find Tcl" with the python.org Python 3.14 on Windows).
    """
    out_dir = os.path.join(ROOT, "build", "tcl-library")
    try:
        res = subprocess.run([sys.executable, os.path.abspath(__file__), "--probe-tcl-tk", out_dir],
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"Warning: couldn't ask Tcl/Tk where their libraries are ({exc})")
        return []
    found = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
    if "TK" not in found and found.get("TCL"):                # (Tk couldn't start: try beside Tcl's library)
        head, tail = os.path.split(found["TCL"].rstrip("/\\"))
        if tail.startswith("tcl"):
            found["TK"] = os.path.join(head, "tk" + tail[3:])
    opts = []
    for key, option, marker in (("TCL", "--tcl-library-dir", "init.tcl"), ("TK", "--tk-library-dir", "tk.tcl")):
        path = found.get(key, "")
        if path and os.path.isfile(os.path.join(path, marker)):
            opts.append(f"{option}={path}")
        else:
            print(f"Warning: no {key.lower()} library found ({found.get(key + 'ERR') or path or res.stderr.strip()[-200:]}); "
                  "leaving it to Nuitka's own search")
    return opts


def command(console=False, tcl_tk=()):
    windows = sys.platform == "win32"
    exe = NAME + (".exe" if windows else ".bin")
    cmd = [
        sys.executable, "-m", "nuitka", "main.py",
        "--onefile",
        f"--output-dir={os.path.join(ROOT, 'build')}",
        f"--output-filename={exe}",
        "--onefile-tempdir-spec={CACHE_DIR}/Hand-thesia/{VERSION}",
        "--include-data-files=CHANGELOG.md=CHANGELOG.md",              # the menu's "What's new"
        "--include-data-files=assets/icon.png=assets/icon.png",        # the window icon
        # tools and tests that the app never imports
        "--nofollow-import-to=pig_eval,learn_weights,tests,conftest,pytest",
        "--noinclude-pytest-mode=nofollow",
        "--noinclude-setuptools-mode=nofollow",
        "--noinclude-data-files=pretty_midi/*.sf2",                   # a soundfont for fluidsynth, unused
        "--assume-yes-for-downloads",
        "--product-name=Hand-thesia",
        f"--product-version={numeric_version()}",
        f"--file-version={numeric_version()}",
        "--file-description=Hand-thesia - falling notes, animated hands and a fingering editor",
        "--company-name=Hand-thesia",
        "--copyright=Hand-thesia",
    ]
    if have_module("tinysoundfont"):                                   # soundfonts (sf_synth.py)
        cmd.append("--include-package=tinysoundfont")
        cmd.append("--nofollow-import-to=pyaudio")                     # (its own player; ours is pygame's mixer)
    if have_tkinter():
        cmd.append("--enable-plugin=tk-inter")                         # the file dialogs
        cmd += list(tcl_tk)
    if windows:
        cmd += [f"--windows-console-mode={'force' if console else 'disable'}",
                "--windows-icon-from-ico=assets/icon.ico"]
    return cmd, exe


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--console", action="store_true", help="keep a console window (Windows)")
    ap.add_argument("--clean", action="store_true", help="start from scratch (delete build/ first)")
    ap.add_argument("--probe-tcl-tk", metavar="DIR", help=argparse.SUPPRESS)     # (internal, see tcl_tk_options)
    args = ap.parse_args()
    if args.probe_tcl_tk:
        return probe_tcl_tk(args.probe_tcl_tk)
    try:
        import nuitka  # noqa: F401
    except ImportError:
        sys.exit("Nuitka isn't installed: pip install -r requirements-build.txt")
    if args.clean:
        shutil.rmtree(os.path.join(ROOT, "build"), ignore_errors=True)
    tcl_tk = []
    if have_tkinter():
        tcl_tk = tcl_tk_options()
        for opt in tcl_tk:
            print("Using", opt)
    else:
        print("Warning: this Python has no tkinter - the built program's file dialogs won't open "
              "(files can still be dropped on its window)")
    cmd, exe = command(args.console, tcl_tk)
    print("Building Hand-thesia", VERSION)
    print(" ".join(cmd))
    subprocess.run(cmd, cwd=ROOT, check=True)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    out = os.path.join(ROOT, "dist", exe)
    shutil.move(os.path.join(ROOT, "build", exe), out)
    print(f"\nDone: {out} ({os.path.getsize(out) / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
