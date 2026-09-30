"""
The program's version: the UTC date and time of the latest commit, as
v20YY.MM.DD.HHMM. Read from git; a GitHub zip download gets the time
filled in by `git archive` (export-subst in .gitattributes).
"""
import os
import subprocess
import time

_ARCHIVE_TIME = "$Format:%ct$"


def _commit_time():
    if _ARCHIVE_TIME.isdigit():
        return int(_ARCHIVE_TIME)
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=os.path.dirname(os.path.abspath(__file__)),
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return int(out.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _version():
    t = _commit_time()
    return time.strftime("v%Y.%m.%d.%H%M", time.gmtime(t)) if t else "v(unknown)"


VERSION = _version()
