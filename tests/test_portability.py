"""Cross-platform checks that can be run from Linux.

The Windows-specific branches are exercised by faking os.name and the tool
locations, so a regression shows up here rather than on someone's laptop.
What this canNOT check is whether the Windows builds of MAFFT, Primer3 and
BLAST+ behave as expected — that needs an actual Windows machine.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import config                                    # noqa: E402
import primer3_runner                            # noqa: E402
from models import Primer3Settings               # noqa: E402


def check(name, fn):
    try:
        fn()
        print("  ok   " + name)
    except AssertionError as exc:
        print("  FAIL " + name + " → " + str(exc))
        raise


def test_batch_files_are_run_through_cmd() -> None:
    """MAFFT for Windows is mafft.bat, which CreateProcess cannot execute."""
    real_which, real_name = config.shutil.which, os.name
    try:
        config.shutil.which = lambda b: r"C:\mafft-win\mafft.bat"
        os.name = "nt"
        os.environ["COMSPEC"] = r"C:\Windows\system32\cmd.exe"
        argv = config.tool_argv("mafft", "--auto", "in.fasta")
        assert argv[0].endswith("cmd.exe"), argv
        assert argv[1] == "/c", argv
        assert argv[2].endswith("mafft.bat"), argv
        assert argv[-2:] == ["--auto", "in.fasta"], argv
    finally:
        config.shutil.which, os.name = real_which, real_name


def test_plain_executables_are_untouched() -> None:
    real_which, real_name = config.shutil.which, os.name
    try:
        config.shutil.which = lambda b: r"C:\blast\bin\blastn.exe"
        os.name = "nt"
        argv = config.tool_argv("blastn", "-version")
        assert argv == [r"C:\blast\bin\blastn.exe", "-version"], argv

        # And on this platform nothing is wrapped either.
        os.name = real_name
        config.shutil.which = lambda b: "/usr/bin/blastn"
        assert config.tool_argv("blastn", "-version") == ["/usr/bin/blastn", "-version"]
    finally:
        config.shutil.which, os.name = real_which, real_name


def test_missing_tool_still_yields_a_runnable_argv() -> None:
    """An unresolvable name is passed through so subprocess raises the error."""
    real_which = config.shutil.which
    try:
        config.shutil.which = lambda b: None
        assert config.tool_argv("nosuchtool", "-x") == ["nosuchtool", "-x"]
    finally:
        config.shutil.which = real_which


def test_windows_config_path_reaches_primer3_correctly() -> None:
    """Primer3 needs a trailing separator; a backslash path must not break it."""
    original = config.PRIMER3_CONFIG
    try:
        config.PRIMER3_CONFIG = r"C:\Program Files\primer3\primer3_config"
        line = next(l for l in primer3_runner.build_boulder(
            "ACGT" * 30, "t", Primer3Settings()).splitlines()
            if l.startswith("PRIMER_THERMODYNAMIC_PARAMETERS_PATH="))
        value = line.split("=", 1)[1]
        assert value.endswith("/"), f"no trailing separator: {value}"
        assert "\\" not in value, f"backslashes left in the boulder file: {value}"
        assert value == "C:/Program Files/primer3/primer3_config/", value

        # A path that already ends in a separator must not gain a second one.
        config.PRIMER3_CONFIG = "/etc/primer3_config/"
        line = next(l for l in primer3_runner.build_boulder(
            "ACGT" * 30, "t", Primer3Settings()).splitlines()
            if l.startswith("PRIMER_THERMODYNAMIC_PARAMETERS_PATH="))
        assert line.split("=", 1)[1] == "/etc/primer3_config/", line
    finally:
        config.PRIMER3_CONFIG = original


def test_config_is_searched_next_to_the_binary() -> None:
    """The Windows Primer3 zip ships primer3_config beside primer3_core.exe."""
    candidates = config._primer3_config_candidates()
    assert any("primer3_config" in c for c in candidates)
    # The directory holding the binary must be among the places searched.
    binary_dir = str(Path(config.PRIMER3_BIN).resolve().parent)
    assert any(c.startswith(binary_dir) for c in candidates), candidates


def test_no_posix_only_module_is_imported() -> None:
    """Importing these on Windows raises ImportError, so none may be used."""
    posix_only = {"pwd", "grp", "fcntl", "termios", "resource"}
    backend = Path(__file__).resolve().parent.parent / "backend"
    for path in backend.glob("*.py"):
        text = path.read_text()
        for module in posix_only:
            assert f"import {module}" not in text, f"{path.name} imports {module}"


if __name__ == "__main__":
    print("cross-platform checks")
    check("Windows batch files are run through cmd.exe",
          test_batch_files_are_run_through_cmd)
    check("plain executables are passed through unchanged",
          test_plain_executables_are_untouched)
    check("an unresolvable tool name is passed through",
          test_missing_tool_still_yields_a_runnable_argv)
    check("a Windows primer3_config path reaches Primer3 correctly",
          test_windows_config_path_reaches_primer3_correctly)
    check("primer3_config is searched next to the binary",
          test_config_is_searched_next_to_the_binary)
    check("no POSIX-only module is imported", test_no_posix_only_module_is_imported)
    print("all portability checks passed")
