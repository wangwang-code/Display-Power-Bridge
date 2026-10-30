[CmdletBinding()]
param(
    [string]$Python = "",
    [string]$UpxPath = "",
    [switch]$NoDownloadUpx,
    [switch]$KeepBuild
)

$RequiredPythonVersion = "3.14.3"
$ErrorActionPreference = "Stop"
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $MyInvocation.MyCommand.Path))
$SourcePath = Join-Path $ProjectRoot "display_power_bridge.py"
$HooksRoot = Join-Path $ProjectRoot "build_hooks"
$BuildRoot = Join-Path $ProjectRoot ".build"
$StageRoot = Join-Path $BuildRoot "pyinstaller"
$StageNoUpxRoot = Join-Path $BuildRoot "pyinstaller-noupx"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$UpxCacheRoot = Join-Path $BuildRoot "upx"
$UpxCacheExe = Join-Path $UpxCacheRoot "upx.exe"

function Remove-SafeDirectory([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $root = [IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside the project: $full"
    }
    Remove-Item -LiteralPath $full -Recurse -Force
}

function Test-PythonPyInstaller($Candidate) {
    try {
        $args = @($Candidate.Args) + @("-c", "import PyInstaller; print(PyInstaller.__version__)")
        $output = & $Candidate.Command @args 2>$null
        if ($LASTEXITCODE -eq 0 -and $output) {
            $Candidate.Version = ($output | Select-Object -First 1).ToString().Trim()
            return $true
        }
    } catch {
    }
    return $false
}

function Get-PythonCandidate {
    if ($Python) {
        if (-not (Test-Path -LiteralPath $Python) -and -not (Get-Command $Python -ErrorAction SilentlyContinue)) {
            throw "Python executable was not found: $Python"
        }
        $candidates = @([pscustomobject]@{ Command = $Python; Args = @(); Version = ""; PythonVersion = "" })
    } else {
        $candidates = @(
            [pscustomobject]@{ Command = "py"; Args = @("-3.14"); Version = ""; PythonVersion = "" },
            [pscustomobject]@{ Command = "python"; Args = @(); Version = ""; PythonVersion = "" }
        )
    }
    foreach ($candidate in $candidates) {
        if (Get-Command $candidate.Command -ErrorAction SilentlyContinue) {
            try {
                $versionArgs = @($candidate.Args) + @("-c", "import platform; print(platform.python_version())")
                $candidate.PythonVersion = (& $candidate.Command @versionArgs 2>$null | Select-Object -First 1).ToString().Trim()
            } catch {
                continue
            }
            if ($candidate.PythonVersion -eq $RequiredPythonVersion) {
                if (Test-PythonPyInstaller $candidate) {
                    return $candidate
                }
                return $candidate
            }
        }
    }
    throw "Python $RequiredPythonVersion was not found. Install that exact version or pass -Python to its executable."
}

function Ensure-PyInstaller($Candidate) {
    if (Test-PythonPyInstaller $Candidate) {
        return
    }
    Write-Host "Installing PyInstaller for the selected Python..."
    $args = @($Candidate.Args) + @("-m", "pip", "install", "--user", "setuptools<81", "altgraph>=0.17.5", "PyInstaller>=6.22,<7")
    & $Candidate.Command @args
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller installation failed."
    }
    if (-not (Test-PythonPyInstaller $Candidate)) {
        throw "PyInstaller is still unavailable after installation."
    }
}

function Find-Upx {
    if ($UpxPath) {
        $candidate = $UpxPath
        if ((Test-Path -LiteralPath $candidate -PathType Container)) {
            $candidate = Join-Path $candidate "upx.exe"
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
        throw "UPX was not found at the requested path: $UpxPath"
    }
    if ($env:UPX_PATH) {
        $candidate = $env:UPX_PATH
        if ((Test-Path -LiteralPath $candidate -PathType Container)) {
            $candidate = Join-Path $candidate "upx.exe"
        }
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return [IO.Path]::GetFullPath($candidate)
        }
    }
    $command = Get-Command upx.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    if (Test-Path -LiteralPath $UpxCacheExe -PathType Leaf) {
        return $UpxCacheExe
    }
    return $null
}

function Download-FileReliable([string]$Uri, [string]$Destination, [hashtable]$Headers) {
    $partial = $Destination + ".part"
    Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        Write-Host "Downloading with curl.exe (retry and stall detection enabled)..."
        $curlArgs = @(
            "--fail", "--location", "--show-error",
            "--retry", "6", "--retry-delay", "2", "--retry-connrefused",
            "--connect-timeout", "20", "--max-time", "300",
            "--speed-limit", "1024", "--speed-time", "20",
            "--continue-at", "-", "--output", $partial,
            "-H", "User-Agent: DisplayPowerBridge-Build", $Uri
        )
        & $curl.Source @curlArgs
        if ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $partial -PathType Leaf)) {
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return
        }
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
        Write-Warning "curl.exe could not complete the download; trying BITS/PowerShell fallback."
    }

    $bits = Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue
    if ($bits) {
        try {
            Write-Host "Downloading with Background Intelligent Transfer Service..."
            Start-BitsTransfer -Source $Uri -Destination $partial -DisplayName "Display Power Bridge UPX" -ErrorAction Stop
            Move-Item -LiteralPath $partial -Destination $Destination -Force
            return
        } catch {
            Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
            Write-Warning ("BITS download failed: " + $_.Exception.Message)
        }
    }

    Write-Host "Downloading with the PowerShell fallback..."
    Invoke-WebRequest -Uri $Uri -Headers $Headers -OutFile $partial -UseBasicParsing -TimeoutSec 120
    if (-not (Test-Path -LiteralPath $partial -PathType Leaf)) {
        throw "The fallback downloader did not create the archive."
    }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}

function Ensure-Upx {
    $found = Find-Upx
    if ($found) {
        return $found
    }
    if ($NoDownloadUpx) {
        throw "UPX was not found. Set -UpxPath, set UPX_PATH, or omit -NoDownloadUpx to download it automatically."
    }

    Write-Host "UPX was not found; downloading the latest official Windows archive..."
    New-Item -ItemType Directory -Force -Path $UpxCacheRoot | Out-Null
    $tempRoot = Join-Path ([IO.Path]::GetTempPath()) ("display-power-bridge-upx-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Force -Path $tempRoot | Out-Null
    try {
        $headers = @{ "User-Agent" = "DisplayPowerBridge-Build" }
        $release = Invoke-RestMethod -Uri "https://api.github.com/repos/upx/upx/releases/latest" -Headers $headers -UseBasicParsing -TimeoutSec 60
        $asset = $release.assets | Where-Object { $_.name -match "win64\.zip$" } | Select-Object -First 1
        if (-not $asset) {
            $asset = $release.assets | Where-Object { $_.name -match "win32\.zip$" } | Select-Object -First 1
        }
        if (-not $asset) {
            throw "The latest UPX release has no Windows zip asset."
        }
        $archive = Join-Path $tempRoot $asset.name
        Download-FileReliable -Uri $asset.browser_download_url -Destination $archive -Headers $headers
        Expand-Archive -LiteralPath $archive -DestinationPath $tempRoot -Force
        $downloaded = Get-ChildItem -LiteralPath $tempRoot -Filter "upx.exe" -Recurse -File | Select-Object -First 1
        if (-not $downloaded) {
            throw "The downloaded UPX archive did not contain upx.exe."
        }
        Copy-Item -LiteralPath $downloaded.FullName -Destination $UpxCacheExe -Force
    } finally {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
    return $UpxCacheExe
}

function Invoke-PyInstallerBuild([bool]$UseUpx, [string]$Stage, [string[]]$ExcludedModules) {
    Remove-SafeDirectory $Stage
    $stageDist = Join-Path $Stage "dist"
    $stageWork = Join-Path $Stage "work"
    $stageSpec = Join-Path $Stage "spec"
    New-Item -ItemType Directory -Force -Path $stageDist, $stageWork, $stageSpec, $ReleaseRoot | Out-Null

    $label = if ($UseUpx) { "UPX" } else { "uncompressed" }
    Write-Host ("Building {0} variant with Python {1} / PyInstaller {2}" -f $label, $pythonCandidate.PythonVersion, $pythonCandidate.Version)
    $args = @($pythonCandidate.Args) + @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--optimize", "2",
        "--name", "DisplayPowerBridge",
        "--distpath", $stageDist,
        "--workpath", $stageWork,
        "--specpath", $stageSpec,
        "--additional-hooks-dir", $HooksRoot,
        $SourcePath
    )
    if ($UseUpx) {
        $args += @("--upx-dir", (Split-Path -Parent $upxExe))
    } else {
        $args += "--noupx"
    }
    foreach ($module in $ExcludedModules) {
        $args += @("--exclude-module", $module)
    }
    $savedPath = $env:PATH
    try {
        $env:PATH = (($savedPath -split ';') | Where-Object {
            $_ -and ($_ -notmatch '(?i)(java|javapath|zulu[^\\/]*|graalvm[^\\/]*|Windows Kits)')
        }) -join ';'
        & $pythonCandidate.Command @args
        if ($LASTEXITCODE -ne 0) {
            throw ("PyInstaller {0} build failed." -f $label)
        }
    } finally {
        $env:PATH = $savedPath
    }
    $built = Join-Path $stageDist "DisplayPowerBridge.exe"
    if (-not (Test-Path -LiteralPath $built -PathType Leaf)) {
        throw ("PyInstaller {0} build produced no executable." -f $label)
    }
    return $built
}

if (-not (Test-Path -LiteralPath $SourcePath -PathType Leaf)) {
    throw "Source file was not found: $SourcePath"
}

$pythonCandidate = Get-PythonCandidate
Ensure-PyInstaller $pythonCandidate
$upxExe = Ensure-Upx
$excludedModules = @(
    "ssl", "_ssl", "hashlib", "_hashlib",
    "bz2", "_bz2", "lzma", "_lzma",
    "decimal", "_decimal",
    "zstandard", "_zstd",
    "select", "_socket", "unicodedata",
    "inspect", "pyi_rth_inspect",
    "dataclasses", "email", "http", "urllib", "importlib.metadata",
    "pydoc", "pprint", "statistics", "random", "netrc", "ftplib",
    "gzip", "tarfile", "pickle", "pickletools"
)
$builtCompressed = Invoke-PyInstallerBuild $true $StageRoot $excludedModules
$builtUncompressed = Invoke-PyInstallerBuild $false $StageNoUpxRoot $excludedModules

$compressedExe = Join-Path $ReleaseRoot "DisplayPowerBridge.exe"
for ($copyAttempt = 1; $copyAttempt -le 5; $copyAttempt++) {
    try {
        Copy-Item -LiteralPath $builtCompressed -Destination $compressedExe -Force -ErrorAction Stop
        break
    } catch {
        if ($copyAttempt -eq 5) { throw }
        Start-Sleep -Milliseconds (250 * $copyAttempt)
    }
}

if ((Get-Item -LiteralPath $compressedExe).Length -le 0) {
    throw "The compressed executable is empty."
}
Write-Host "PyInstaller completed; UPX was applied to eligible bundled binaries."

Copy-Item -LiteralPath (Join-Path $ProjectRoot "README.md") -Destination (Join-Path $ReleaseRoot "README.md") -Force
$metadata = [ordered]@{
    product = "Display Power Bridge"
    built_at_utc = [DateTime]::UtcNow.ToString("o")
    python = $pythonCandidate.PythonVersion
    python_command = $pythonCandidate.Command
    pyinstaller = $pythonCandidate.Version
    upx = (& $upxExe "--version" | Select-Object -First 1).ToString().Trim()
    excluded_modules = $excludedModules
    pruned_assets = @(
        "Tcl timezone database (_tcl_data/tzdata)",
        "Tcl optional packages (opt0.4, http1.0, platform, tcltest)",
        "unused Tk demo/logo images",
        "unused Tcl message locales (kept en, zh, zh_cn)",
        "unused Tcl encoding tables",
        "unused optional Tk scripts",
        "unused Tcl HTTP package and Tk file-dialog helper"
    )
    compressed_bytes = (Get-Item -LiteralPath $compressedExe).Length
    uncompressed_bytes = (Get-Item -LiteralPath $builtUncompressed).Length
    size_reduction_percent = [math]::Round((1 - ((Get-Item -LiteralPath $compressedExe).Length / (Get-Item -LiteralPath $builtUncompressed).Length)) * 100, 2)
    compressed_sha256 = (Get-FileHash -LiteralPath $compressedExe -Algorithm SHA256).Hash
    uncompressed_sha256 = (Get-FileHash -LiteralPath $builtUncompressed -Algorithm SHA256).Hash
}
$metadata | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $ReleaseRoot "build-metadata.json") -Encoding utf8

if (-not $KeepBuild) {
    Remove-SafeDirectory $StageRoot
    Remove-SafeDirectory $StageNoUpxRoot
}

Write-Host "Build complete."
Write-Host "Compressed:   $compressedExe"
Write-Host "UPX:          $upxExe"
