from __future__ import annotations

import base64
import os
import re
import shutil
import subprocess
from pathlib import Path


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


def move_file_to_shell_folder(source: str | Path, folder: str) -> str:
    source_path = Path(source)
    _copy_to_shell_folder(source_path, folder)
    return f"{folder}\\{source_path.name}"


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
$deadline = (Get-Date).AddSeconds($timeoutSeconds)
$lastSize = -1
$stableChecks = 0
do {
    Start-Sleep -Seconds 2
    $dest = Resolve-ShellFolder $destPath
    $item = if ($null -ne $dest) { $dest.ParseName($fileName) } else { $null }
    if ($null -ne $item) {
        $size = $item.Size
        if ($size -eq $sourceSize -and $size -eq $lastSize) {
            $stableChecks += 1
        } else {
            $stableChecks = 0
        }
        $lastSize = $size
    }
    if ($stableChecks -ge 2) {
        Remove-Item -LiteralPath $sourcePath -Force
        exit 0
    }
} while ((Get-Date) -lt $deadline)
exit 1
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
    subprocess.Popen(
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
    )


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
