"""Where the app keeps the user's data: beside the code from source, apart from the program when compiled."""
import os

import paths


def test_from_source_the_data_stays_beside_the_code():
    assert paths.user_data_dir(compiled=False) == paths.RESOURCE_DIR
    assert os.path.exists(paths.resource("CHANGELOG.md")) and os.path.exists(paths.resource("assets", "icon.png"))


def test_compiled_the_data_goes_to_appdata_or_a_portable_folder(tmp_path):
    exe = tmp_path / "app"
    exe.mkdir()
    env = {"APPDATA": str(tmp_path / "Roaming")}
    assert paths.user_data_dir(True, str(exe), "win32", env) == os.path.join(env["APPDATA"], "Hand-thesia")
    assert paths.user_data_dir(True, str(exe), "linux", {"XDG_DATA_HOME": str(tmp_path / "share")}) == \
        os.path.join(str(tmp_path / "share"), "hand-thesia")
    (exe / "pianists").mkdir()                          # portable: a pianists folder next to the program
    assert paths.user_data_dir(True, str(exe), "win32", env) == str(exe)


def test_data_from_before_the_rename_is_moved_over(tmp_path):
    # %APPDATA%\\Piano Animator (the app's old name) becomes %APPDATA%\\Hand-thesia, pianists and all
    exe = tmp_path / "app"
    exe.mkdir()
    roaming = tmp_path / "Roaming"
    (roaming / "Piano Animator" / "pianists").mkdir(parents=True)
    (roaming / "Piano Animator" / "pianists" / "Me.json").write_text("{}")
    got = paths.user_data_dir(True, str(exe), "win32", {"APPDATA": str(roaming)})
    assert got == str(roaming / "Hand-thesia")
    assert (roaming / "Hand-thesia" / "pianists" / "Me.json").exists()
    assert not (roaming / "Piano Animator").exists()
    # ...only once: with both there, the new one is used
    (roaming / "Piano Animator").mkdir()
    assert paths.user_data_dir(True, str(exe), "win32", {"APPDATA": str(roaming)}) == str(roaming / "Hand-thesia")


def test_build_version_is_four_numbers():
    import build
    from version import VERSION
    v = build.numeric_version()
    assert len(v.split(".")) == 4 and all(p.isdigit() for p in v.split("."))
    assert v.startswith(VERSION.lstrip("v"))
    cmd, exe = build.command()
    assert "--onefile" in cmd and any(c.startswith("--include-data-files=CHANGELOG.md") for c in cmd)


def test_tcl_tk_libraries_built_into_the_dll_are_copied_out(tmp_path):
    # Tcl/Tk 9 (the python.org Python 3.14 on Windows) keep their script library inside the DLL
    import build

    class Interp:
        calls = []

        def eval(self, script):
            self.calls.append(script)
            return ""
    dest = str(tmp_path / "tcl")
    assert build._real_library(Interp(), "/usr/share/tcltk/tcl8.6", dest) == "/usr/share/tcltk/tcl8.6"
    assert Interp.calls == []                                  # a real folder is used as it is
    assert build._real_library(Interp(), "//zipfs:/lib/tcl/library", dest) == dest
    assert Interp.calls == ["file copy -force {//zipfs:/lib/tcl/library} {%s}" % dest.replace(os.sep, "/")]


def test_build_passes_the_tcl_and_tk_folders():
    import build
    cmd, _ = build.command(tcl_tk=["--tcl-library-dir=/x/tcl9.0", "--tk-library-dir=/x/tk9.0"])
    if build.have_tkinter():
        assert "--tcl-library-dir=/x/tcl9.0" in cmd and "--tk-library-dir=/x/tk9.0" in cmd
