from __future__ import annotations

import os
import subprocess

import pytest

from transmutary.store import permissions
from transmutary.store.permissions import PrivatePermissionError


def test_windows_acl_hardening_replaces_dacl_with_sid_allowlist(monkeypatch, tmp_path):
    target = tmp_path / "artifacts"
    target.mkdir()
    calls: list[list[str]] = []

    monkeypatch.setattr(permissions, "IS_WINDOWS", True)
    monkeypatch.setattr(permissions.shutil, "which", lambda name: "powershell.exe")

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = '"USER","S-1-5-21-1000"\n' if cmd[0] == "whoami" else "ok"
            stderr = ""

        return Result()

    monkeypatch.setattr(permissions.subprocess, "run", fake_run)

    permissions.harden_windows_acl(str(target))

    assert calls[0] == ["whoami", "/user", "/fo", "csv", "/nh"]
    ps_cmd = calls[1]
    assert ps_cmd[0] == "powershell.exe"
    script = ps_cmd[-1]
    assert "$acl.SetAccessRuleProtection($true, $false)" in script
    assert "$sids = @('S-1-5-21-1000', 'S-1-5-18', 'S-1-5-32-544')" in script
    assert "[System.IO.Directory]::SetAccessControl" in script
    assert "[System.IO.File]::SetAccessControl" in script
    assert "Everyone" not in script
    assert "Authenticated Users" not in script


def test_windows_acl_hardening_fails_without_powershell(monkeypatch, tmp_path):
    target = tmp_path / "state.sqlite3"
    target.write_bytes(b"")
    monkeypatch.setattr(permissions, "IS_WINDOWS", True)
    monkeypatch.setattr(permissions.shutil, "which", lambda name: None)

    with pytest.raises(PrivatePermissionError, match="PowerShell"):
        permissions.harden_windows_acl(str(target))


def test_windows_acl_hardening_uses_powershell_sid_fallback(monkeypatch, tmp_path):
    target = tmp_path / "state.sqlite3"
    target.write_bytes(b"")
    calls: list[list[str]] = []
    monkeypatch.setattr(permissions, "IS_WINDOWS", True)
    monkeypatch.setattr(permissions.shutil, "which", lambda name: "powershell.exe")

    def fake_run(cmd, capture_output, text, check):
        calls.append(cmd)

        class Result:
            returncode = 1 if cmd[0] == "whoami" else 0
            stdout = "S-1-5-21-2000" if len(calls) == 2 else "ok"
            stderr = ""

        return Result()

    monkeypatch.setattr(permissions.subprocess, "run", fake_run)

    permissions.harden_windows_acl(str(target))

    assert calls[0][0] == "whoami"
    assert "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value" in calls[1][-1]
    assert "'S-1-5-21-2000'" in calls[2][-1]


def test_posix_enforce_private_mode_rejects_group_bits(monkeypatch, tmp_path):
    target = tmp_path / "state.sqlite3"
    target.write_bytes(b"")
    os.chmod(target, 0o644)
    monkeypatch.setattr(permissions, "IS_WINDOWS", False)

    with pytest.raises(PrivatePermissionError, match="0o600"):
        permissions.enforce_private_mode(str(target), 0o600, "State DB")


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL integration")
@pytest.mark.real_windows_acl
def test_real_windows_acl_replaces_existing_broad_acl(tmp_path):
    target = tmp_path / "acl-real"
    target.mkdir()
    subprocess.run(["icacls", str(target), "/grant", "Users:(RX)"], check=True)

    permissions.harden_windows_acl(str(target))

    result = subprocess.run(["icacls", str(target)], capture_output=True, text=True, check=True)
    output = result.stdout
    assert "BUILTIN\\Users" not in output
    assert "NT AUTHORITY\\Authenticated Users" not in output
