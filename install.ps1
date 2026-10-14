$ErrorActionPreference = 'Stop'
$root = [IO.Path]::GetFullPath($PSScriptRoot)
$logs = Join-Path $root 'logs'
$transcriptStarted = $false
$installSucceeded = $false
$savedConsoleEncoding = [Console]::OutputEncoding
$savedOutputEncoding = $OutputEncoding
$changedVariables = @('PATH', 'TEMP', 'TMP', 'PYTHONHOME', 'PYTHONPATH',
    'PYTHONNOUSERSITE', 'PYTHONDONTWRITEBYTECODE', 'PIP_CONFIG_FILE',
    'PIP_CACHE_DIR', 'PYINSTALLER_CONFIG_DIR', 'QT_PLUGIN_PATH',
    'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORM')
$savedVariables = @{}
foreach ($variableName in $changedVariables) {
    $savedVariables[$variableName] = [Environment]::GetEnvironmentVariable($variableName, 'Process')
}

function Invoke-LocalPython {
    param([string[]]$Arguments)
    & $script:python -I -B -X utf8 @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Python command failed with exit code $LASTEXITCODE" }
}

function Get-LocalSha256 {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '')
    } finally {
        $algorithm.Dispose()
        $stream.Dispose()
    }
}

function Expand-LocalRuntime {
    param([string]$ArchivePath, [string]$Destination)
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $destinationPrefix = [IO.Path]::GetFullPath($Destination).TrimEnd('\') + '\'
    [IO.Directory]::CreateDirectory($Destination) | Out-Null
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $archive.Entries) {
            $targetPath = [IO.Path]::GetFullPath((Join-Path $Destination $entry.FullName))
            if (-not $targetPath.StartsWith($destinationPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Unexpected runtime archive path.'
            }
            if ($entry.FullName.EndsWith('/')) {
                [IO.Directory]::CreateDirectory($targetPath) | Out-Null
            } else {
                [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($targetPath)) | Out-Null
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $targetPath, $true)
            }
        }
    } finally { $archive.Dispose() }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem -or
        $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
        throw 'This package requires Windows 10/11 x64 (Intel/AMD).'
    }
    if ([Environment]::OSVersion.Version.Major -lt 10) {
        throw 'This package requires Windows 10/11 x64.'
    }
    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    Start-Transcript -Path (Join-Path $logs 'install.log') -Force | Out-Null
    $transcriptStarted = $true
    $OutputEncoding = New-Object Text.UTF8Encoding $false
    [Console]::OutputEncoding = $OutputEncoding
    $localTemp = Join-Path $root 'temp'
    $cache = Join-Path $root 'cache'
    $build = Join-Path $root 'build'
    foreach ($directory in @($localTemp, $cache, $build)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $env:TEMP = $localTemp
    $env:TMP = $localTemp
    $env:PYTHONHOME = $null
    $env:PYTHONPATH = $null
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PIP_CONFIG_FILE = 'NUL'
    $env:PIP_CACHE_DIR = $cache
    $env:PYINSTALLER_CONFIG_DIR = $cache
    $env:QT_PLUGIN_PATH = $null
    $env:QT_QPA_PLATFORM_PLUGIN_PATH = $null
    $env:QT_QPA_PLATFORM = $null

    Write-Host '[1/5] Verifying offline package files...'
    $manifest = Get-Content -LiteralPath (Join-Path $root 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $rootPrefix = $root.TrimEnd('\') + '\'
    foreach ($entry in $manifest.files) {
        $filePath = [IO.Path]::GetFullPath((Join-Path $root $entry.path))
        if (-not $filePath.StartsWith($rootPrefix, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Invalid package file path: $($entry.path)"
        }
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "Missing package file: $($entry.path). Extract the complete ZIP first."
        }
        if ((Get-LocalSha256 -Path $filePath) -ne $entry.sha256) {
            throw "Package file checksum mismatch: $($entry.path). Extract a fresh copy of the ZIP."
        }
    }

    Write-Host '[2/5] Preparing local Python runtime...'
    $runtime = Join-Path $root 'runtime'
    $pythonDirectory = Join-Path $runtime 'tools'
    $script:python = Join-Path $pythonDirectory 'python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        Expand-LocalRuntime -ArchivePath (Join-Path $root 'packages\python-3.12.10-amd64.zip') -Destination $runtime
    }
    $env:PATH = "$pythonDirectory;$pythonDirectory\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    Invoke-LocalPython -Arguments @('-c', 'import sys; assert sys.version_info[:3] == (3,12,10); assert sys.maxsize > 2**32; print(sys.version)')

    Write-Host '[3/5] Installing bundled packages (offline, local folder only)...'
    Invoke-LocalPython -Arguments @((Join-Path $root 'bootstrap_pip.py'),
        'install', '--disable-pip-version-check', '--no-index', '--no-cache-dir',
        '--only-binary=:all:', '--no-compile', '--no-warn-script-location',
        '--find-links', (Join-Path $root 'packages\wheels'), '--require-hashes',
        '-r', (Join-Path $root 'requirements-offline.txt'))
    Invoke-LocalPython -Arguments @('-m', 'pip', '--disable-pip-version-check', 'check')

    Write-Host '[4/5] Building Windows executable...'
    $staging = Join-Path $localTemp 'dist'
    Invoke-LocalPython -Arguments @((Join-Path $root 'build_local.py'), '--noconfirm', '--onedir',
        '--windowed', '--noupx', '--contents-directory', 'app_runtime',
        '--name', 'IQCS16_spectrum', '--distpath', $staging,
        '--workpath', $build, '--specpath', $build, '--paths', $root,
        (Join-Path $root 'exe_entry.py'))

    Write-Host '[5/5] Testing executable and publishing files...'
    $stagedDirectory = Join-Path $staging 'IQCS16_spectrum'
    $stagedExe = Join-Path $stagedDirectory 'IQCS16_spectrum.exe'
    $testReport = Join-Path $logs 'exe_test_report.json'
    if (Test-Path -LiteralPath $testReport) { Remove-Item -LiteralPath $testReport -Force }
    $testProcess = Start-Process -FilePath $stagedExe -ArgumentList @('--self-test', ('"' + $testReport + '"')) -WorkingDirectory $root -WindowStyle Hidden -PassThru
    if (-not $testProcess.WaitForExit(60000)) {
        $testProcess.Kill()
        throw 'Executable self-test timed out. See logs\install.log.'
    }
    if ($testProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $testReport)) {
        throw "Executable self-test failed (exit code $($testProcess.ExitCode)). See logs\exe_test_report.json."
    }
    $testResult = Get-Content -LiteralPath $testReport -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $testResult.success) { throw 'Executable self-test reported a failure.' }
    Copy-Item -LiteralPath (Join-Path $stagedDirectory 'app_runtime') -Destination $root -Recurse -Force
    Copy-Item -LiteralPath $stagedExe -Destination (Join-Path $root 'IQCS16_spectrum.exe') -Force
    $installRecord = [ordered]@{
        installed_at = (Get-Date).ToString('o')
        python = '3.12.10 x64'
        executable = 'IQCS16_spectrum.exe'
        executable_sha256 = Get-LocalSha256 -Path (Join-Path $root 'IQCS16_spectrum.exe')
        tests_run = $testResult.tests_run
        tests_skipped = $testResult.skipped
        offline = $true
    }
    $installRecord | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logs 'installation.json') -Encoding UTF8
    Write-Host ''
    Write-Host 'SUCCESS: Double-click IQCS16_spectrum.exe in this folder.' -ForegroundColor Green
    Write-Host 'Keep app_runtime beside the EXE. All installed files are in this folder.'
    $installSucceeded = $true
} catch {
    Write-Host ''
    Write-Host ('INSTALLATION FAILED: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host 'Keep the whole folder and check logs\install.log for details.'
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
    foreach ($variableName in $changedVariables) {
        [Environment]::SetEnvironmentVariable($variableName, $savedVariables[$variableName], 'Process')
    }
    [Console]::OutputEncoding = $savedConsoleEncoding
    $OutputEncoding = $savedOutputEncoding
}
if (-not $installSucceeded) { exit 1 }
exit 0
