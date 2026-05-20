from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SHELL_TARGET_PREFIX = "shell:"


@dataclass(frozen=True)
class ShellMoveResult:
    path: str
    name: str
    suffix: str
    modified_time: float

    def __str__(self) -> str:
        return self.path


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


def move_file_to_folder(source: str | Path, folder: str | Path) -> Path | ShellMoveResult:
    return move_files_to_folder([source], folder)[0]


def move_files_to_folder(sources: list[str | Path], folder: str | Path) -> list[Path | ShellMoveResult]:
    if is_shell_target(folder):
        return move_files_to_shell_folder(sources, folder)
    return [_move_file_to_filesystem_folder(source, folder) for source in sources]


def _move_file_to_filesystem_folder(source: str | Path, folder: str | Path) -> Path:
    source_path = Path(source)
    target_root = Path(folder)
    target_root.mkdir(parents=True, exist_ok=True)
    if source_path.parent.resolve() == target_root.resolve():
        return source_path
    destination = unique_destination(target_root, source_path.name)
    shutil.move(str(source_path), str(destination))
    return destination


def is_shell_target(folder: str | Path) -> bool:
    return str(folder).startswith(SHELL_TARGET_PREFIX)


def shell_target_path(folder: str | Path) -> str:
    return str(folder)[len(SHELL_TARGET_PREFIX) :]


def browse_shell_folder() -> str:
    script = r"""
$shell = New-Object -ComObject Shell.Application
$folder = $shell.BrowseForFolder(0, "Choose Switch or portable device folder", 0, 17)
if ($null -eq $folder) {
    exit 2
}
$item = $folder.Self
$path = $item.Path
if ([string]::IsNullOrWhiteSpace($path)) {
    $path = $item.Name
}
[pscustomobject]@{
    name = $item.Name
    path = $path
} | ConvertTo-Json -Compress
"""
    result = _run_powershell(script, allow_cancel=True)
    if result.returncode == 2:
        return ""
    try:
        payload = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        raise RuntimeError("Could not read the selected device folder from Windows.") from exc
    return f"{SHELL_TARGET_PREFIX}{payload['path']}"


def move_file_to_shell_folder(source: str | Path, folder: str | Path) -> ShellMoveResult:
    return move_files_to_shell_folder([source], folder)[0]


def move_files_to_shell_folder(sources: list[str | Path], folder: str | Path) -> list[ShellMoveResult]:
    source_paths = [Path(source) for source in sources]
    for source_path in source_paths:
        if not source_path.exists():
            raise FileNotFoundError(str(source_path))
    target_path = shell_target_path(folder)
    script = r"""
param(
    [string]$SourcesJson,
    [string]$TargetPath
)

$shell = New-Object -ComObject Shell.Application
$target = $shell.NameSpace($TargetPath)
if ($null -eq $target) {
    function Find-ShellFolderByPath {
        param(
            [Parameter(Mandatory=$true)] $Folder,
            [Parameter(Mandatory=$true)] [string] $Needle,
            [int] $Depth = 0
        )
        if ($Depth -gt 8) {
            return $null
        }
        foreach ($child in @($Folder.Items())) {
            if ($child.IsFolder) {
                $childPath = [string]$child.Path
                $childName = [string]$child.Name
                if ($childPath -eq $Needle -or $childName -eq $Needle -or $Needle.EndsWith("\" + $childName)) {
                    $candidate = $child.GetFolder
                    if ($null -ne $candidate) {
                        return $candidate
                    }
                }
                $nested = $null
                try {
                    $nestedFolder = $child.GetFolder
                    if ($null -ne $nestedFolder) {
                        $nested = Find-ShellFolderByPath -Folder $nestedFolder -Needle $Needle -Depth ($Depth + 1)
                    }
                } catch {
                    $nested = $null
                }
                if ($null -ne $nested) {
                    return $nested
                }
            }
        }
        return $null
    }

    $thisPc = $shell.NameSpace(17)
    if ($null -ne $thisPc) {
        $target = Find-ShellFolderByPath -Folder $thisPc -Needle $TargetPath
    }
}
if ($null -eq $target) {
    throw "Windows cannot open the selected device folder. Reconnect the device, unlock/enable USB transfer if needed, then choose Settings > Install folder > Browse Device again."
}

$sourcePaths = @($SourcesJson | ConvertFrom-Json)
foreach ($sourcePath in $sourcePaths) {
    $source = Get-Item -LiteralPath ([string]$sourcePath)
    if ($null -ne $target.ParseName($source.Name)) {
        throw "A file named '$($source.Name)' already exists in the selected device folder."
    }
    $target.CopyHere($source.FullName, 0)

    $copied = $null
    for ($index = 0; $index -lt 720; $index++) {
        Start-Sleep -Milliseconds 500
        $copied = $target.ParseName($source.Name)
        if ($null -ne $copied) {
            break
        }
    }
    if ($null -eq $copied) {
        throw "Windows started the device copy but the copied file was not visible before the timeout."
    }

    Remove-Item -LiteralPath $source.FullName -Force
}
"""
    _run_powershell(script, json.dumps([str(path) for path in source_paths]), target_path, timeout=21600)
    modified_time = time.time()
    return [
        ShellMoveResult(
            path=f"{SHELL_TARGET_PREFIX}{target_path}\\{source_path.name}",
            name=source_path.name,
            suffix=source_path.suffix.lower(),
            modified_time=modified_time,
        )
        for source_path in source_paths
    ]


def moved_file_modified_time(destination: str | Path | ShellMoveResult) -> float:
    if isinstance(destination, ShellMoveResult):
        return destination.modified_time
    return Path(destination).stat().st_mtime


def _run_powershell(
    script: str,
    *args: str,
    timeout: int = 120,
    allow_cancel: bool = False,
) -> subprocess.CompletedProcess[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False, encoding="utf-8") as script_file:
        script_file.write(script)
        script_path = script_file.name
    command = [
        "powershell",
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_path,
        *args,
    ]
    try:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                "Windows did not finish the device copy before the timeout. "
                "If the Switch shows no transfer activity, choose the specific writable storage folder with Browse Device "
                "or use an SD card/local staging folder."
            ) from exc
        if result.returncode != 0 and not (allow_cancel and result.returncode == 2):
            message = (result.stderr or result.stdout or "Windows Shell operation failed.").strip()
            raise RuntimeError(message)
        return result
    finally:
        Path(script_path).unlink(missing_ok=True)


def delete_file_if_present(path: str | Path) -> bool:
    file_path = Path(path)
    if not file_path.exists():
        return False
    file_path.unlink()
    return True
