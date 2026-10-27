[CmdletBinding()]
param(
    # The GUI.Forms Windows toolchain (LLVM 22.1.8, MSYS2 CLANG64) extracted at
    # its producer's msys root. System header paths enter the compiler-cache
    # hash, so the provider's published objects hit only at that location.
    [string]$MsysRoot = $env:FILE_MANAGER_MSYS_ROOT,
    [string]$GoBin = $env:FILE_MANAGER_GO_BIN,
    [string]$RustBin = (Join-Path $env:USERPROFILE '.cargo\bin')
)

# Dot-source to prepare only this session. The toolchain tree stays read-only.
# Go and Rust are optional: only explicit research workflows use them; the
# shipped services come from the pinned backend bundle.
$ErrorActionPreference = 'Stop'
[string]$fileManagerRoot = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($MsysRoot)) {
    $MsysRoot = 'D:\a\_temp\msys64'
}
[string]$fileManagerClangBin = Join-Path $MsysRoot 'clang64\bin'
[string]$fileManagerUsrBin = Join-Path $MsysRoot 'usr\bin'
[string]$fileManagerRequiredTool = ''
foreach ($fileManagerRequiredTool in @('clang.exe', 'clang++.exe', 'cmake.exe', 'ninja.exe', 'ccache.exe', 'python.exe')) {
    if (-not (Test-Path -LiteralPath (Join-Path $fileManagerClangBin $fileManagerRequiredTool) -PathType Leaf)) {
        throw "Missing CLANG64 tool $fileManagerRequiredTool in $fileManagerClangBin. Extract the GUI.Forms Windows toolchain (gui_forms/tools/windows_toolchain.py) and set FILE_MANAGER_MSYS_ROOT to its msys64 directory."
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $fileManagerUsrBin 'sh.exe') -PathType Leaf)) {
    throw "Missing MSYS2 shell in $fileManagerUsrBin; the toolkit's dependency fetch scripts need it."
}
if ([string]::IsNullOrWhiteSpace($GoBin)) {
    $GoBin = [Environment]::GetEnvironmentVariable('FILE_MANAGER_GO_BIN', 'User')
}

# The provider's consumer contract: clang64\bin, then usr\bin, ahead of PATH.
[System.Collections.Generic.List[string]]$fileManagerToolBins = [System.Collections.Generic.List[string]]::new()
[string]$fileManagerToolBin = ''
foreach ($fileManagerToolBin in @($fileManagerClangBin, $fileManagerUsrBin, $GoBin, $RustBin)) {
    if (-not [string]::IsNullOrWhiteSpace($fileManagerToolBin) -and
        (Test-Path -LiteralPath $fileManagerToolBin -PathType Container)) {
        [System.Management.Automation.PathInfo]$fileManagerResolved = Resolve-Path -LiteralPath $fileManagerToolBin
        $fileManagerToolBins.Add($fileManagerResolved.Path)
    }
}
[string[]]$fileManagerOriginalBins = $env:PATH -split ';'
[string]$fileManagerOriginalBin = ''
foreach ($fileManagerOriginalBin in $fileManagerOriginalBins) {
    if (-not [string]::IsNullOrEmpty($fileManagerOriginalBin)) {
        $fileManagerToolBins.Add($fileManagerOriginalBin)
    }
}
# Keep the first occurrence, so explicitly selected tools precede inherited ones.
[System.Collections.Generic.HashSet[string]]$fileManagerSeenBins = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
[System.Collections.Generic.List[string]]$fileManagerUniqueBins = [System.Collections.Generic.List[string]]::new()
foreach ($fileManagerToolBin in $fileManagerToolBins) {
    if ($fileManagerSeenBins.Add($fileManagerToolBin)) {
        $fileManagerUniqueBins.Add($fileManagerToolBin)
    }
}
$env:PATH = [string]::Join(';', $fileManagerUniqueBins)
$env:FILE_MANAGER_MSYS_ROOT = $MsysRoot
# Compiler names, launchers and cache settings exactly as provider CI sets them.
$env:CC = 'clang'
$env:CXX = 'clang++'
$env:OBJCXX = 'clang++'
$env:CMAKE_C_COMPILER_LAUNCHER = 'ccache'
$env:CMAKE_CXX_COMPILER_LAUNCHER = 'ccache'
$env:CMAKE_OBJCXX_COMPILER_LAUNCHER = 'ccache'
[string]$fileManagerCacheBase = $fileManagerRoot.Replace('\', '/')
$env:CCACHE_BASEDIR = $fileManagerCacheBase
$env:CCACHE_DIR = $fileManagerCacheBase + '/.ccache'
$env:CCACHE_COMPILERCHECK = 'content'
$env:CCACHE_MAXSIZE = '2G'
if (-not [string]::IsNullOrWhiteSpace($GoBin)) {
    $env:FILE_MANAGER_GO_BIN = $GoBin
}

[string]$fileManagerToolName = ''
foreach ($fileManagerToolName in @('cmake', 'ninja', 'ccache', 'clang', 'clang++', 'python', 'sh', 'gh', 'go', 'cargo')) {
    [System.Management.Automation.ApplicationInfo[]]$fileManagerTools = @(Get-Command $fileManagerToolName -CommandType Application -ErrorAction SilentlyContinue)
    if ($fileManagerTools.Count -eq 0) {
        Write-Warning "$fileManagerToolName is not available in this session."
    } else {
        [System.Management.Automation.ApplicationInfo]$fileManagerTool = $fileManagerTools[0]
        Write-Host ('{0}: {1}' -f $fileManagerToolName, $fileManagerTool.Source)
    }
}
