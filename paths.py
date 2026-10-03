"""
paths.py - Where the app finds its own files and keeps the user's.

Run from source, everything stays where it always was: the bundled files
(CHANGELOG.md, assets/) beside the modules, the user's pianists and settings
in pianists/ next to them.

Compiled into one .exe (Nuitka --onefile, see build.py), the modules and the
bundled files are unpacked to a cache folder - fine to read, but not a place
to keep anything. The user's data then goes to
  - a `pianists` folder next to the .exe, if there is one (portable use: make
    the folder and the app keeps everything beside itself), else
  - %APPDATA%\\Hand-thesia on Windows (~/.local/share/hand-thesia
    elsewhere). A folder from before the app was renamed (Piano Animator,
    piano-animator) is moved there the first time.
"""
import os
import sys

APP_NAME = "Hand-thesia"
OLD_NAMES = ("Piano Animator", "piano-animator")     # (Windows, elsewhere: the folders before the rename)

# Nuitka defines __compiled__ in every module it compiles; other freezers set sys.frozen
COMPILED = "__compiled__" in globals() or bool(getattr(sys, "frozen", False))

# bundled, read-only files: CHANGELOG.md, assets/
RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))


def exe_dir():
    """The folder the program was started from (the .exe's, when compiled)."""
    return os.path.dirname(os.path.abspath(sys.argv[0] if sys.argv and sys.argv[0] else __file__))


def user_data_dir(compiled=COMPILED, exe_folder=None, platform=sys.platform, env=os.environ, migrate=True):
    """Where the user's data goes (see the module docstring)."""
    if not compiled:
        return RESOURCE_DIR
    portable = exe_folder or exe_dir()
    if os.path.isdir(os.path.join(portable, "pianists")):
        return portable
    if platform == "win32":
        base = env.get("APPDATA") or os.path.expanduser("~")
        new, old = os.path.join(base, APP_NAME), os.path.join(base, OLD_NAMES[0])
    else:
        base = env.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        new, old = os.path.join(base, "hand-thesia"), os.path.join(base, OLD_NAMES[1])
    if migrate and not os.path.exists(new) and os.path.isdir(old):
        try:
            os.rename(old, new)                 # (the pianists made before the rename come along)
        except OSError:
            return old                          # (can't move it - in use? - so keep using it there)
    return new


DATA_DIR = user_data_dir()          # the user's pianists/, settings and the log live here


def resource(*parts):
    """Path of a bundled file, e.g. resource("assets", "icon.png")."""
    return os.path.join(RESOURCE_DIR, *parts)


def log_path():
    return os.path.join(DATA_DIR, "hand-thesia.log")
