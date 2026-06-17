param(
    [string]$OutputDirectory = "",
    [string]$Prefix = "project",
    [switch]$IncludeExistingZipFiles,
    [switch]$ExcludeLegacyQuarantine
)

$ErrorActionPreference = "Stop"

Set-StrictMode -Version Latest

$Root = (Get-Location).Path

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = $Root
}

$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

if (-not (Test-Path -LiteralPath $OutputDirectory)) {
    New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
}

$Stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$ZipPath = Join-Path $OutputDirectory "$Prefix`_$Stamp.zip"
$ZipPath = [System.IO.Path]::GetFullPath($ZipPath)

function Test-AllowedName {
    param([string]$Name)

    if ([string]::IsNullOrWhiteSpace($Name)) {
        return $false
    }

    if ($Name.StartsWith(".")) {
        return $false
    }

    if ($Name.StartsWith("_")) {
        return $false
    }

    return $true
}

function Get-RelativeProjectPath {
    param([string]$FullName)

    $Full = [System.IO.Path]::GetFullPath($FullName)
    if (-not $Full.StartsWith($Root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path is outside project root: $Full"
    }

    return $Full.Substring($Root.Length).TrimStart("\", "/")
}

function Test-IsLegacyQuarantineRoot {
    param([string]$FullName)

    $Relative = Get-RelativeProjectPath -FullName $FullName
    return $Relative -eq "legacy_quarantine"
}

Add-Type -AssemblyName System.IO.Compression.FileSystem

$RootItem = Get-Item -LiteralPath $Root
$PendingDirectories = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
$Files = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
$PendingDirectories.Push($RootItem)

$SkippedDirectoryCount = 0
$SkippedFileCount = 0
$SkippedZipCount = 0
$SkippedLegacyCount = 0

while ($PendingDirectories.Count -gt 0) {
    $Directory = $PendingDirectories.Pop()

    foreach ($Child in Get-ChildItem -LiteralPath $Directory.FullName -Force) {
        if ($Child.PSIsContainer) {
            if (-not (Test-AllowedName -Name $Child.Name)) {
                $SkippedDirectoryCount += 1
                continue
            }

            if ($ExcludeLegacyQuarantine -and (Test-IsLegacyQuarantineRoot -FullName $Child.FullName)) {
                $SkippedLegacyCount += 1
                continue
            }

            $PendingDirectories.Push($Child)
            continue
        }

        if (-not (Test-AllowedName -Name $Child.Name)) {
            $SkippedFileCount += 1
            continue
        }

        $ChildFullName = [System.IO.Path]::GetFullPath($Child.FullName)

        if ($ChildFullName -eq $ZipPath) {
            continue
        }

        if ((-not $IncludeExistingZipFiles) -and ($Child.Extension -ieq ".zip")) {
            $SkippedZipCount += 1
            continue
        }

        $Files.Add($Child)
    }
}

Write-Host "Project root: $Root"
Write-Host "Output zip:   $ZipPath"
Write-Host "Files:        $($Files.Count)"
Write-Host "Skipped dirs: $SkippedDirectoryCount"
Write-Host "Skipped files by .* or _*: $SkippedFileCount"
Write-Host "Skipped existing zip files: $SkippedZipCount"

if ($ExcludeLegacyQuarantine) {
    Write-Host "Skipped legacy_quarantine: $SkippedLegacyCount"
}

if ($Files.Count -eq 0) {
    throw "No files matched archive rules."
}

if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}

$Zip = [System.IO.Compression.ZipFile]::Open(
    $ZipPath,
    [System.IO.Compression.ZipArchiveMode]::Create
)

try {
    foreach ($File in $Files) {
        $EntryName = (Get-RelativeProjectPath -FullName $File.FullName) -replace "\\", "/"
        [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $Zip,
            $File.FullName,
            $EntryName,
            [System.IO.Compression.CompressionLevel]::Optimal
        ) | Out-Null
    }
}
