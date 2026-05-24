from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MtpStorageInfo:
    name: str
    free_bytes: int
    total_bytes: int
    path: str = ""


MTP_INSTALL_DESTINATIONS = {
    "nand": ("NAND Install", "NAND install"),
    "sd": ("SD Card Install", "SD install"),
}


def is_shell_path(value: str | Path) -> bool:
    text = str(value).strip().lower()
    return text.startswith("shell:::") or text.startswith("::{")


def unique_destination(folder: str | Path, filename: str) -> Path:
    root = Path(folder)
    candidate = root / filename
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    index = 1
    while True:
        next_candidate = root / f"{stem} ({index}){suffix}"
        if not next_candidate.exists():
            return next_candidate
        index += 1


def move_file_to_folder(source: str | Path, folder: str | Path) -> Path | str:
    if is_shell_path(folder):
        return move_file_to_shell_folder(source, str(folder))
    source_path = Path(source)
    target_root = Path(folder)
    target_root.mkdir(parents=True, exist_ok=True)
    if source_path.parent.resolve() == target_root.resolve():
        return source_path
    destination = unique_destination(target_root, source_path.name)
    shutil.move(str(source_path), str(destination))
    return destination


def move_files_to_folder(sources: list[str | Path], folder: str | Path) -> list[Path | str]:
    if is_shell_path(folder):
        return move_files_to_shell_folder(sources, str(folder))
    return [move_file_to_folder(source, folder) for source in sources]


def move_file_to_shell_folder(source: str | Path, folder: str) -> str:
    source_path = Path(source)
    _copy_to_shell_folder(source_path, folder)
    return f"{folder}\\{source_path.name}"


def move_files_to_shell_folder(sources: list[str | Path], folder: str) -> list[str]:
    source_paths = [Path(source) for source in sources]
    _copy_files_to_shell_folder(source_paths, folder)
    return [f"{folder}\\{source_path.name}" for source_path in source_paths]


def mtp_storage_status(timeout_seconds: int = 8) -> str:
    return _format_mtp_storage_status(list_mtp_storage_info(timeout_seconds=timeout_seconds))


def mtp_destination_storage_info(folder: str | Path, timeout_seconds: int = 8) -> MtpStorageInfo | None:
    destination = _normalize_shell_compare_path(str(folder))
    matches = []
    for storage in list_mtp_storage_info(timeout_seconds=timeout_seconds):
        storage_path = _normalize_shell_compare_path(storage.path)
        if storage_path and (destination == storage_path or destination.startswith(f"{storage_path}\\")):
            matches.append(storage)
    if not matches:
        return None
    return max(matches, key=lambda storage: len(_normalize_shell_compare_path(storage.path)))


def mtp_install_destination_info(destination: str, timeout_seconds: int = 8) -> MtpStorageInfo | None:
    target = _normalize_mtp_install_destination(destination)
    if not target:
        return None
    _, storage_name = MTP_INSTALL_DESTINATIONS[target]
    for storage in list_mtp_storage_info(timeout_seconds=timeout_seconds):
        if storage.name.casefold() == storage_name.casefold():
            return storage
    return None


def mtp_install_destination_label(destination: str) -> str:
    target = _normalize_mtp_install_destination(destination)
    if not target:
        return ""
    return MTP_INSTALL_DESTINATIONS[target][0]


def _normalize_mtp_install_destination(destination: str) -> str:
    text = (destination or "").strip().casefold().replace("_", " ").replace("-", " ")
    if "nand" in text:
        return "nand"
    if text in {"sd", "sd card"} or "sd card" in text or "sd install" in text:
        return "sd"
    return text if text in MTP_INSTALL_DESTINATIONS else ""


def list_mtp_storage_info(timeout_seconds: int = 8) -> list[MtpStorageInfo]:
    script = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$shell = New-Object -ComObject Shell.Application
$root = $shell.Namespace("shell:MyComputerFolder")
if ($null -eq $root) {
    Write-Output "[]"
    exit 0
}
function Get-ExtendedValue($item, [string[]]$keys) {
    foreach ($key in $keys) {
        try {
            $value = $item.ExtendedProperty($key)
            if ($null -ne $value -and "$value" -ne "") {
                return $value
            }
        } catch {}
    }
    return $null
}
function Convert-SizeTextToBytes($value) {
    if ($null -eq $value) {
        return 0
    }
    if ($value -is [int64] -or $value -is [uint64] -or $value -is [int] -or $value -is [uint32] -or $value -is [double] -or $value -is [decimal]) {
        return [int64]$value
    }
    $text = "$value"
    if ($text -match '^\d+$') {
        return [int64]$text
    }
    if ($text -match '([\d\.,]+)\s*(B|KB|MB|GB|TB)') {
        $number = [double]($matches[1].Replace(',', ''))
        switch ($matches[2].ToUpperInvariant()) {
            "B" { return [int64]$number }
            "KB" { return [int64]($number * 1KB) }
            "MB" { return [int64]($number * 1MB) }
            "GB" { return [int64]($number * 1GB) }
            "TB" { return [int64]($number * 1TB) }
        }
    }
    return 0
}
function Get-DetailValue($folder, $item, [string[]]$patterns) {
    for ($index = 0; $index -lt 300; $index++) {
        $header = $folder.GetDetailsOf($null, $index)
        if ([string]::IsNullOrWhiteSpace($header)) {
            continue
        }
        foreach ($pattern in $patterns) {
            if ($header -match $pattern) {
                $value = $folder.GetDetailsOf($item, $index)
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        }
    }
    return $null
}
function Normalize-StorageName($deviceName, $storageName) {
    $name = "$storageName"
    if ($name -match '(?i)\bsd\b|sd card|microsd' -and $name -match '(?i)install') {
        return "SD install"
    }
    if ($name -match '(?i)nand' -and $name -match '(?i)install') {
        return "NAND install"
    }
    return ""
}
$rows = @()
foreach ($device in $root.Items()) {
    $deviceName = "$($device.Name)"
    $devicePath = "$($device.Path)"
    $deviceLooksLikeSwitch = $deviceName -match '(?i)switch|nintendo' -or $devicePath -match '(?i)vid_057e|pid_201d'
    if (-not $deviceLooksLikeSwitch) {
        continue
    }
    $deviceFolder = $null
    try { $deviceFolder = $device.GetFolder } catch {}
    if ($null -eq $deviceFolder) {
        continue
    }
    foreach ($storage in $deviceFolder.Items()) {
        $displayName = Normalize-StorageName $deviceName $storage.Name
        if (-not $displayName) {
            continue
        }
        $free = Get-ExtendedValue $storage @("System.FreeSpace", "System.Storage.FreeSpace", "System.Volume.FreeSpace")
        $total = Get-ExtendedValue $storage @("System.Capacity", "System.Storage.Capacity", "System.Volume.TotalSize", "System.Size")
        if ($null -eq $free) {
            $free = Get-DetailValue $deviceFolder $storage @("(?i)free")
        }
        if ($null -eq $total) {
            $total = Get-DetailValue $deviceFolder $storage @("(?i)total", "(?i)capacity", "(?i)^size$")
        }
        $freeBytes = Convert-SizeTextToBytes $free
        $totalBytes = Convert-SizeTextToBytes $total
        if ($freeBytes -gt 0 -and $totalBytes -gt 0) {
            $rows += [PSCustomObject]@{
                name = $displayName
                free_bytes = $freeBytes
                total_bytes = $totalBytes
                path = "$($storage.Path)"
            }
        }
    }
}
$rows | ConvertTo-Json -Compress
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    output = result.stdout.strip()
    if not output:
        return []
    try:
        payload = json.loads(output.splitlines()[-1])
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    rows = []
    payload_items = payload if isinstance(payload, list) else []
    for item in payload_items:
        try:
            name = str(item.get("name") or "").strip()
            free_bytes = int(item.get("free_bytes") or 0)
            total_bytes = int(item.get("total_bytes") or 0)
            path = str(item.get("path") or "").strip()
        except (AttributeError, TypeError, ValueError):
            continue
        if name and free_bytes > 0 and total_bytes > 0:
            rows.append(MtpStorageInfo(name, free_bytes, total_bytes, path))
    return rows


def _normalize_shell_compare_path(value: str) -> str:
    text = value.strip().replace("/", "\\")
    if text.lower().startswith("shell:"):
        text = text[6:]
    return text.rstrip("\\").lower()


def _format_mtp_storage_status(rows: list[MtpStorageInfo]) -> str:
    if not rows:
        return ""
    ordered = sorted(rows, key=lambda row: _mtp_storage_sort_key(row.name))
    return " | ".join(
        f"{row.name}: {_format_bytes(row.free_bytes)} free / {_format_bytes(row.total_bytes)}"
        for row in ordered
    )


def _mtp_storage_sort_key(name: str) -> tuple[int, str]:
    lowered = name.lower()
    if "nand" in lowered:
        return (0, lowered)
    if lowered == "sd" or "sd" in lowered:
        return (1, lowered)
    return (2, lowered)


def _format_bytes(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"


def _copy_to_shell_folder(source_path: Path, folder: str, timeout_seconds: int = 1800) -> None:
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    script = r"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$destPath = $env:SWITCH_CATALOG_MTP_DESTINATION
$sourcePath = $env:SWITCH_CATALOG_MTP_SOURCE
$timeoutSeconds = [int]$env:SWITCH_CATALOG_MTP_TIMEOUT
$fileName = [System.IO.Path]::GetFileName($sourcePath)
$sourceSize = (Get-Item -LiteralPath $sourcePath).Length
$shell = New-Object -ComObject Shell.Application
Add-Type @"
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Text;

public static class SwitchCatalogWindowProbe {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool EnumWindows(EnumWindowsProc enumProc, IntPtr lParam);

    [DllImport("user32.dll")]
    public static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int count);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int count);

    public static string[] VisibleWindows() {
        List<string> rows = new List<string>();
        EnumWindows(delegate(IntPtr hWnd, IntPtr lParam) {
            if (!IsWindowVisible(hWnd)) {
                return true;
            }
            StringBuilder title = new StringBuilder(512);
            StringBuilder className = new StringBuilder(256);
            GetWindowText(hWnd, title, title.Capacity);
            GetClassName(hWnd, className, className.Capacity);
            rows.Add(className.ToString() + "\t" + title.ToString());
            return true;
        }, IntPtr.Zero);
        return rows.ToArray();
    }
}
"@
function Get-FileOperationWindowCount {
    $windows = [SwitchCatalogWindowProbe]::VisibleWindows()
    return @(
        $windows | Where-Object {
            $_ -match '^OperationStatusWindow\t' -or
            $_ -match '(?i)\b(copying|moving|calculating|replacing)\b'
        }
    ).Count
}
function Get-EstimatedInstallWait([int64]$bytes, [int]$timeoutSeconds) {
    $seconds = [int][Math]::Ceiling($bytes / 12MB) + 45
    if ($seconds -lt 30) {
        $seconds = 30
    }
    if ($seconds -gt $timeoutSeconds) {
        $seconds = $timeoutSeconds
    }
    return $seconds
}
function Wait-ForShellFileOperation([int]$timeoutSeconds, [int64]$sourceSize) {
    $seenWindow = $false
    $startDeadline = (Get-Date).AddSeconds(20)
    do {
        if ((Get-FileOperationWindowCount) -gt 0) {
            $seenWindow = $true
            break
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $startDeadline)
    if (-not $seenWindow) {
        Start-Sleep -Seconds (Get-EstimatedInstallWait $sourceSize $timeoutSeconds)
        return
    }
    $deadline = (Get-Date).AddSeconds($timeoutSeconds)
    do {
        Start-Sleep -Seconds 1
        if ((Get-FileOperationWindowCount) -eq 0) {
            Start-Sleep -Seconds 2
            return
        }
    } while ((Get-Date) -lt $deadline)
    throw "Windows file transfer window did not close before the $timeoutSeconds second timeout."
}
function Resolve-ShellFolder($path) {
    $paths = @($path)
    if ($path.StartsWith("shell:")) {
        $paths += $path.Substring(6)
    }
    foreach ($candidate in $paths) {
        $folder = $shell.Namespace($candidate)
        if ($null -ne $folder) {
            return $folder
        }
    }
    foreach ($candidate in $paths) {
        $lastSlash = $candidate.LastIndexOf("\")
        if ($lastSlash -lt 0) {
            continue
        }
        $parentPath = $candidate.Substring(0, $lastSlash)
        $parent = $shell.Namespace($parentPath)
        if ($null -eq $parent) {
            continue
        }
        foreach ($item in $parent.Items()) {
            if ($item.Path -eq $candidate -or ("shell:" + $item.Path) -eq $path) {
                return $item.GetFolder
            }
        }
    }
    return $null
}
$dest = Resolve-ShellFolder $destPath
if ($null -eq $dest) {
    throw "MTP destination is not available: $destPath"
}
if ($null -ne $dest.ParseName($fileName)) {
    throw "A file named '$fileName' already exists on the MTP destination."
}
$dest.CopyHere($sourcePath, 16)
Wait-ForShellFileOperation $timeoutSeconds $sourceSize
"""
    encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    env = os.environ.copy()
    env.update(
        {
            "SWITCH_CATALOG_MTP_DESTINATION": folder,
            "SWITCH_CATALOG_MTP_SOURCE": str(source_path),
            "SWITCH_CATALOG_MTP_TIMEOUT": str(timeout_seconds),
        }
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-STA",
                "-ExecutionPolicy",
                "Bypass",
                "-EncodedCommand",
                encoded_script,
            ],
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            capture_output=True,
            text=True,
            timeout=timeout_seconds + 30,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"MTP transfer timed out for '{source_path.name}' after {timeout_seconds} seconds."
        ) from exc
    if result.returncode != 0:
        message = _clean_powershell_message(result.stderr) or _clean_powershell_message(result.stdout)
        if not message:
            message = f"PowerShell exited with status {result.returncode}."
        raise RuntimeError(f"MTP transfer failed for '{source_path.name}': {message}")


def _copy_files_to_shell_folder(source_paths: list[Path], folder: str, timeout_seconds: int = 1800) -> None:
    for index, source_path in enumerate(source_paths):
        _copy_to_shell_folder(source_path, folder, timeout_seconds=timeout_seconds)
        if index < len(source_paths) - 1:
            time.sleep(1)


def _clean_powershell_message(message: str) -> str:
    message = message.strip()
    if not message.startswith("#< CLIXML"):
        return message
    decoded = message.replace("_x000D__x000A_", "\n")
    errors = re.findall(r'<S S="Error">(.*?)</S>', decoded, flags=re.DOTALL)
    if not errors:
        return message
    return "\n".join(_strip_xml_text(error) for error in errors).strip()


def _strip_xml_text(value: str) -> str:
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .replace("&quot;", '"')
        .replace("&apos;", "'")
    )


def delete_file_if_present(path: str | Path) -> bool:
    file_path = Path(path)
    if not file_path.exists():
        return False
    file_path.unlink()
    return True
