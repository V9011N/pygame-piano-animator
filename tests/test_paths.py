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
    assert paths.user_data_dir(True, str(exe), "win32", env) == os.path.join(env["APPDATA"], "Piano Animator")
    assert paths.user_data_dir(True, str(exe), "linux", {"XDG_DATA_HOME": str(tmp_path / "share")}) == \
        os.path.join(str(tmp_path / "share"), "piano-animator")
    (exe / "pianists").mkdir()                          # portable: a pianists folder next to the program
    assert paths.user_data_dir(True, str(exe), "win32", env) == str(exe)


def test_build_version_is_four_numbers():
    import build
    from version import VERSION
    v = build.numeric_version()
    assert len(v.split(".")) == 4 and all(p.isdigit() for p in v.split("."))
    assert v.startswith(VERSION.lstrip("v"))
    cmd, exe = build.command()
    assert "--onefile" in cmd and any(c.startswith("--include-data-files=CHANGELOG.md") for c in cmd)
