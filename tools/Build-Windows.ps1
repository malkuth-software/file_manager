[CmdletBinding()]
param(
    [ValidateRange(1, 64)]
    [int]$Jobs = 4,
    [ValidateRange(1, 64)]
    [int]$TestJobs = 2,
    # Frontend-only development package; the receipt then lists no services.
    [switch]$SkipComponents,
    # Restore only the backend bundle, keeping the local compiler cache.
    [switch]$SkipCache
)

# The local Windows route is the CI route: the pinned toolchain from
# Enter-WindowsToolchain.ps1, the pinned inputs, then tools/build_native.py,
# which builds the toolkit with GUI.Forms' own ci_build.py.
$ErrorActionPreference = 'Stop'
[string]$fileManagerToolchainScript = Join-Path $PSScriptRoot 'Enter-WindowsToolchain.ps1'
. $fileManagerToolchainScript
[string]$fileManagerRoot = Split-Path $PSScriptRoot -Parent

function Invoke-FileManagerBuildCommand {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE"
    }
}

Push-Location $fileManagerRoot
try {
    [System.Collections.Generic.List[string]]$fileManagerFetch = [System.Collections.Generic.List[string]]::new()
    $fileManagerFetch.AddRange([string[]]@('-B', 'tools/fetch_build_inputs.py', '--platform', 'windows-x64'))
    if ($SkipCache) {
        $fileManagerFetch.Add('--skip-cache')
    }
    if (-not $SkipComponents) {
        Invoke-FileManagerBuildCommand 'python' $fileManagerFetch.ToArray()
    }
    [System.Collections.Generic.List[string]]$fileManagerBuild = [System.Collections.Generic.List[string]]::new()
    $fileManagerBuild.AddRange([string[]]@('-B', 'tools/build_native.py', '--jobs', "$Jobs", '--test-jobs', "$TestJobs"))
    if ($SkipComponents) {
        $fileManagerBuild.Add('--skip-components')
    }
    Invoke-FileManagerBuildCommand 'python' $fileManagerBuild.ToArray()
} finally {
    Pop-Location
}
