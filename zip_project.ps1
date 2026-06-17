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

function Test-AllowedProjectPath {
    param([string]$FullName)

    $Relative = Get-RelativeProjectPath -FullName $FullName
    if ([string]::IsNullOrWhiteSpace($Relative)) {
        return $false
    }

    $Parts = $Relative -split "[\\/]"

    foreach ($Part in $Parts) {
        if (-not (Test-AllowedName -Name $Part)) {
            return $false
        }
    }

    if ($ExcludeLegacyQuarantine -and $Parts.Count -gt 0 -and $Parts[0] -eq "legacy_quarantine") {
        return $false
    }

    return $true
}

function Copy-ProjectFileToStage {
    param(
        [System.IO.FileInfo]$File,
        [string]$StageRoot
    )

    $Relative = Get-RelativeProjectPath -FullName $File.FullName
    $Destination = Join-Path $StageRoot $Relative
    $DestinationDirectory = Split-Path -Parent $Destination

    if (-not (Test-Path -LiteralPath $DestinationDirectory)) {
        New-Item -ItemType Directory -Force -Path $DestinationDirectory | Out-Null
    }

    Copy-Item -LiteralPath $File.FullName -Destination $Destination -Force
}

$SkippedDirectoryCount = 0
$SkippedFileByNameCount = 0
$SkippedZipCount = 0
$SkippedLegacyCount = 0
$SkippedOutputZipCount = 0

$Files = New-Object 'System.Collections.Generic.List[System.IO.FileInfo]'
$PendingDirectories = New-Object 'System.Collections.Generic.Stack[System.IO.DirectoryInfo]'
$PendingDirectories.Push((Get-Item -LiteralPath $Root))

while ($PendingDirectories.Count -gt 0) {
    $Directory = $PendingDirectories.Pop()

    foreach ($Child in Get-ChildItem -LiteralPath $Directory.FullName -Force) {
        if ($Child.PSIsContainer) {
            if (-not (Test-AllowedName -Name $Child.Name)) {
                $SkippedDirectoryCount += 1
                continue
            }

            if ($ExcludeLegacyQuarantine) {
                $RelativeDirectory = Get-RelativeProjectPath -FullName $Child.FullName
                if ($RelativeDirectory -eq "legacy_quarantine") {
                    $SkippedLegacyCount += 1
                    continue
                }
            }

            $PendingDirectories.Push($Child)
            continue
        }

        if (-not (Test-AllowedName -Name $Child.Name)) {
            $SkippedFileByNameCount += 1
            continue
        }

        $ChildFullName = [System.IO.Path]::GetFullPath($Child.FullName)

        if ($ChildFullName -eq $ZipPath) {
            $SkippedOutputZipCount += 1
            continue
        }

        if ((-not $IncludeExistingZipFiles) -and ($Child.Extension -ieq ".zip")) {
            $SkippedZipCount += 1
            continue
        }

        if (-not (Test-AllowedProjectPath -FullName $Child.FullName)) {
            $SkippedFileByNameCount += 1
            continue
        }

        $Files.Add($Child)
    }
}

Write-Host "Project root: $Root"
Write-Host "Output zip:   $ZipPath"
Write-Host "Files:        $($Files.Count)"
Write-Host "Skipped dirs by .* or _*:  $SkippedDirectoryCount"
Write-Host "Skipped files by .* or _*: $SkippedFileByNameCount"
Write-Host "Skipped existing zip files: $SkippedZipCount"
Write-Host "Skipped output zip path:    $SkippedOutputZipCount"

if ($ExcludeLegacyQuarantine) {
    Write-Host "Skipped legacy_quarantine:  $SkippedLegacyCount"
}

if ($Files.Count -eq 0) {
    throw "No files matched archive rules."
