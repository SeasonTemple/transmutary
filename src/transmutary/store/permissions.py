"""Cross-platform private filesystem permission helpers."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess

IS_WINDOWS = os.name == "nt"


class PrivatePermissionError(Exception):
    """Raised when a path cannot be made private enough for persisted data."""


def chmod_private(path: str, mode: int, *, inherited_ok: bool = False) -> None:
    """Apply private POSIX mode bits, or a restrictive Windows DACL."""
    if IS_WINDOWS:
        if inherited_ok:
            return
        harden_windows_acl(path)
        return
    os.chmod(path, mode)


def enforce_private_mode(path: str, mode: int, label: str) -> None:
    """Reject overly broad permissions where POSIX mode bits are meaningful."""
    if IS_WINDOWS:
        return
    current = stat.S_IMODE(os.stat(path).st_mode)
    if current & 0o077:
        raise PrivatePermissionError(
            f"{label} {path!r} has permissions {oct(current)}; require {oct(mode)}."
        )


def harden_windows_acl(path: str) -> None:
    """Restrict a Windows path to the current user, SYSTEM, and Administrators.

    PowerShell/.NET is used instead of a Python dependency so the package remains
    lightweight. The DACL is replaced with an allow-list rather than edited in
    place, so stale explicit ACEs cannot remain after hardening succeeds.
    """
    if not IS_WINDOWS:
        return
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        raise PrivatePermissionError("PowerShell is required to harden Windows file ACLs")

    current_user_sid = _current_windows_user_sid()
    script = _acl_script(path, current_user_sid, inherit_to_children=os.path.isdir(path))
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise PrivatePermissionError(f"Windows ACL hardening failed for {path!r}: {detail}")


def _current_windows_user_sid() -> str:
    result = subprocess.run(
        ["whoami", "/user", "/fo", "csv", "/nh"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        parts = [part.strip().strip('"') for part in result.stdout.strip().split(",")]
        if len(parts) >= 2 and parts[1].startswith("S-"):
            return parts[1]

    # Fallback for unusual environments where whoami.exe is unavailable.
    script = (
        "[Security.Principal.WindowsIdentity]::GetCurrent().User.Value"
    )
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if powershell is None:
        raise PrivatePermissionError("cannot resolve current Windows user SID")
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    sid = result.stdout.strip()
    if result.returncode == 0 and sid.startswith("S-"):
        return sid
    detail = (result.stderr or result.stdout).strip()
    raise PrivatePermissionError(f"cannot resolve current Windows user SID: {detail}")


def _acl_script(path: str, current_user_sid: str, *, inherit_to_children: bool) -> str:
    escaped_path = _ps_single_quote(path)
    escaped_user_sid = _ps_single_quote(current_user_sid)
    inheritance = (
        "[System.Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'"
        if inherit_to_children
        else "[System.Security.AccessControl.InheritanceFlags]::None"
    )
    return f"""
$path = '{escaped_path}'
$acl = New-Object System.Security.AccessControl.FileSecurity
if ((Get-Item -LiteralPath $path).PSIsContainer) {{
    $acl = New-Object System.Security.AccessControl.DirectorySecurity
}}
$acl.SetAccessRuleProtection($true, $false)
$inheritance = {inheritance}
$propagation = [System.Security.AccessControl.PropagationFlags]::None
$right = [System.Security.AccessControl.FileSystemRights]::FullControl
$allow = [System.Security.AccessControl.AccessControlType]::Allow
$sids = @('{escaped_user_sid}', 'S-1-5-18', 'S-1-5-32-544')
foreach ($sidText in $sids) {{
    $sid = New-Object System.Security.Principal.SecurityIdentifier($sidText)
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $sid, $right, $inheritance, $propagation, $allow
    )
    $acl.AddAccessRule($rule)
}}
if ((Get-Item -LiteralPath $path).PSIsContainer) {{
    [System.IO.Directory]::SetAccessControl((Resolve-Path -LiteralPath $path), $acl)
}} else {{
    [System.IO.File]::SetAccessControl((Resolve-Path -LiteralPath $path), $acl)
}}
"""


def _ps_single_quote(value: str) -> str:
    return value.replace("'", "''")
