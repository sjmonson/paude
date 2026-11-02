"""Isolated tests for portable Git configuration snapshots."""

import subprocess
from unittest.mock import patch

import pytest

from paude.git_config import global_git_snapshot


def test_global_sources_and_round_trip(tmp_path, monkeypatch):
    monkeypatch.delenv("GIT_CONFIG_GLOBAL")
    monkeypatch.setenv("HOME", str(tmp_path))
    xdg = tmp_path / "xdg-config" / "git"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg.parent))
    xdg.mkdir(parents=True)
    (xdg / "config").write_text("[test]\n value = xdg\n")
    (tmp_path / "included").write_text("[test]\n value = included\n")
    (tmp_path / ".gitconfig").write_text(
        "[include]\n path = included\n"
        '[test "a.b"]\n flag\n empty =\n value = "a\\nb\\t\\\\\\""\n'
        "[test]\n value = home\n"
    )
    with global_git_snapshot() as snapshot:
        assert snapshot is not None
        saved = snapshot
        content = snapshot.read_text()
        assert "include" not in content.replace('"included"', "")
        result = subprocess.run(
            ["git", "config", "--file", str(snapshot), "--null", "--list"],
            capture_output=True,
            check=True,
        )
        assert result.stdout == (
            b"test.value\nxdg\0test.value\nincluded\0test.a.b.flag\0"
            b'test.a.b.empty\n\0test.a.b.value\na\nb\t\\"\0test.value\nhome\0'
        )
    assert not saved.exists()


def test_empty_snapshot():
    with global_git_snapshot() as snapshot:
        assert snapshot is not None
        assert snapshot.read_text() == ""


def test_failed_dump_is_discarded():
    result = subprocess.CompletedProcess(
        [], 128, "test.partial\nsecret\0", "bad config"
    )
    with patch("paude.git_config.subprocess.run", return_value=result):
        with pytest.warns(UserWarning, match="Unable to read"):
            with global_git_snapshot() as snapshot:
                assert snapshot is None


def test_missing_git():
    with patch("paude.git_config.subprocess.run", side_effect=FileNotFoundError):
        with pytest.warns(UserWarning, match="Unable to read"):
            with global_git_snapshot() as snapshot:
                assert snapshot is None


def test_nested_and_conditional_includes_exclude_local(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "config", "--local", "test.local", "excluded"], check=True)
    (tmp_path / "nested").write_text("[test]\n nested = yes\n")
    (tmp_path / "included").write_text("[include]\n path = nested\n")
    (tmp_path / "test.gitconfig").write_text(
        f'[includeIf "gitdir:{tmp_path}/"]\n path = included\n'
        '[includeIf "gitdir:/does-not-match/"]\n path = missing\n'
    )
    with global_git_snapshot() as snapshot:
        assert snapshot is not None
        assert snapshot.read_text() == '[test]\n\tnested = "yes"\n'
