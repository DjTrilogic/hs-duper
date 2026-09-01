"""The command table.

Three commands - link, ping, await - were written, documented in the usage text
and never routed, so running them printed the usage and did nothing. This is
the check that would have caught it.
"""

import inspect
import re

from hsduper import __main__ as cli


def listed_commands() -> set[str]:
    return set(re.findall(r"^  python -m hsduper (\w+)", cli.USAGE, re.M))


def dispatched_commands() -> set[str]:
    source = inspect.getsource(cli.main)
    single = set(re.findall(r'command == "(\w+)"', source))
    grouped = set(re.findall(r'"(\w+)"', "".join(re.findall(r"command in \(([^)]*)\)", source))))
    return single | grouped


def test_every_documented_command_is_routed():
    missing = listed_commands() - dispatched_commands()
    assert not missing, f"documented but never dispatched: {sorted(missing)}"


def test_every_routed_command_is_documented():
    extra = dispatched_commands() - listed_commands()
    assert not extra, f"dispatched but undocumented: {sorted(extra)}"


def test_no_argv_prints_usage_and_succeeds(capsys):
    assert cli.main([]) == 0
    assert "python -m hsduper" in capsys.readouterr().out


def test_an_unknown_command_prints_usage_and_fails(capsys):
    assert cli.main(["nonsense"]) == 1
    assert "python -m hsduper" in capsys.readouterr().out


def test_a_missing_config_is_an_error_not_a_traceback(capsys, monkeypatch, tmp_path):
    """Every command needing config must fail with a sentence, not a stack."""
    monkeypatch.setattr("hsduper.config.PATH", tmp_path / "nope.json")
    assert cli.main(["scan"]) == 1
    assert "calibrate" in capsys.readouterr().out
