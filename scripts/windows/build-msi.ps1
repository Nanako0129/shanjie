#Requires -Version 7
<#
Builds the Windows installer (windows/installer/shanjie.wxs) in two stages, so that a signing step can sit between them in the workflow
that calls this script (ci.yml's windows-msi job; later the release workflow). The signing steps themselves are not in this script.

  build-msi.ps1 binaries -Out <dir> [-Timing]
      cargo builds shanjie-tip and shanjie-host with the release identity (`release-ids`; `-Timing` adds `timing`, for the marker check only,
      never for a package) and shanjie-installer-ca, in <dir>\cargo (its own --target-dir), then copies the three files a package takes to
      <dir>\bin: shanjie_tip.dll, shanjie-host.exe, installer-ca.dll. SHANJIE_BUILD_ID, when the caller sets it, goes into the build hash.

  build-msi.ps1 msi -Bin <dir> -Out <file.msi> [-Variant A|T] [-Source <wxs>] [-SkipValidation]
      Prints and checks the WiX version (the runner's WiX 3, at least 3.14.1), writes the generated files (CC-BY-SA-4.0-attribution.txt,
      the dialog bitmaps and icons) next to the output, and runs candle and light (zh-TW, ICE20 off: the Dialog table has no FilesInUse
      on purpose). The version is SHANJIE_VERSION and nothing else (MAJOR.MINOR.PATCH within the MSI limits 255.255.65535); without it
      the build fails. A light output naming ICE63 fails the build. -Source and -SkipValidation (light -sval) are for the check's broken
      copies only (ci.yml), never for a package.

Runs in PowerShell 7 on Windows with cargo, and WiX 3 under %WIX%.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory, Position = 0)][ValidateSet('binaries', 'msi')][string]$Stage,
    [Parameter(Mandatory)][string]$Out,
    [string]$Bin,
    [ValidateSet('A', 'T')][string]$Variant = 'A',
    [string]$Source,
    [switch]$Timing,
    [switch]$SkipValidation
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe exited with $LASTEXITCODE" }
}

function Get-Sha256([string]$Path) { (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant() }

if ($Stage -eq 'binaries') {
    $outDir = [IO.Path]::GetFullPath($Out, (Get-Location).Path)
    $features = if ($Timing) { 'release-ids,timing' } else { 'release-ids' }
    Write-Host "SHANJIE_BUILD_ID=$($env:SHANJIE_BUILD_ID) features=$features target-dir=$outDir\cargo"
    # One cargo call: the features apply to shanjie-tip and shanjie-host (shanjie-installer-ca has none and always uses the release identity's table).
    Invoke-Checked 'cargo' @('build', '--release', '--locked', '--target-dir', "$outDir\cargo", '-p', 'shanjie-tip', '-p', 'shanjie-host', '-p', 'shanjie-installer-ca', '--features', $features)
    $binDir = Join-Path $outDir 'bin'
    New-Item -ItemType Directory -Force $binDir | Out-Null
    $release = Join-Path $outDir 'cargo\release'
    foreach ($pair in @(@('shanjie_tip.dll', 'shanjie_tip.dll'), @('shanjie-host.exe', 'shanjie-host.exe'), @('installer_ca.dll', 'installer-ca.dll'))) {
        Copy-Item -LiteralPath (Join-Path $release $pair[0]) -Destination (Join-Path $binDir $pair[1]) -Force
        '{0}  {1}' -f (Get-Sha256 (Join-Path $binDir $pair[1])), $pair[1]
    }
    return
}

# ---- msi ----
if (-not $Bin) { throw '-Bin is required for the msi stage' }
$binDir = (Resolve-Path $Bin).Path
foreach ($f in 'shanjie_tip.dll', 'shanjie-host.exe', 'installer-ca.dll') {
    if (-not (Test-Path -LiteralPath (Join-Path $binDir $f) -PathType Leaf)) { throw "missing $f in $binDir" }
}
if (-not $Source) { $Source = Join-Path $Repo 'windows\installer\shanjie.wxs' }
$Source = (Resolve-Path $Source).Path

$version = $env:SHANJIE_VERSION
if (-not $version -or $version -notmatch '^(\d{1,5})\.(\d{1,5})\.(\d{1,5})$') { throw "SHANJIE_VERSION must be MAJOR.MINOR.PATCH (got '$version')" }
if ([int]$Matches[1] -gt 255 -or [int]$Matches[2] -gt 255 -or [int]$Matches[3] -gt 65535) { throw "SHANJIE_VERSION $version is beyond the MSI limits 255.255.65535" }

if (-not $env:WIX) { throw 'WIX is not set (the runner image sets it to the WiX 3 folder)' }
$wixBin = Join-Path $env:WIX 'bin'
$candle = Join-Path $wixBin 'candle.exe'
$light = Join-Path $wixBin 'light.exe'
$wixVersion = [version](Get-Item -LiteralPath $candle).VersionInfo.FileVersion
Write-Host "WiX: $candle file version $wixVersion"
if ($wixVersion -lt [version]'3.14.1') { throw "WiX $wixVersion is older than 3.14.1" }

$msi = [IO.Path]::GetFullPath($Out, (Get-Location).Path)
New-Item -ItemType Directory -Force ([IO.Path]::GetDirectoryName($msi)) | Out-Null
$work = "$msi.work"
if (Test-Path -LiteralPath $work) { Remove-Item -LiteralPath $work -Recurse -Force }
$gen = Join-Path $work 'gen'
New-Item -ItemType Directory -Force $gen | Out-Null

# ---- CC-BY-SA-4.0-attribution.txt: the text of scripts/build-app.sh (one copy of it), plus the Rust crates linked into the Windows binaries ----
$buildApp = [IO.File]::ReadAllLines((Join-Path $Repo 'scripts\build-app.sh'))
$open = 'cat > "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt" <<''EOF'''
$starts = @(0..($buildApp.Count - 1) | Where-Object { $buildApp[$_] -ceq $open })
if ($starts.Count -ne 1) { throw "build-app.sh: expected one line '$open', found $($starts.Count)" }
$end = $starts[0] + 1
while ($end -lt $buildApp.Count -and $buildApp[$end] -cne 'EOF') { $end++ }
if ($end -ge $buildApp.Count) { throw 'build-app.sh: the attribution text has no closing EOF' }
$appendNsi = 'cat LICENSES/name-suggestion-index-BSD-3-Clause.txt >> "$RES/LICENSES/CC-BY-SA-4.0-attribution.txt"'
if ($end + 1 -ge $buildApp.Count -or $buildApp[$end + 1] -cne $appendNsi) { throw "build-app.sh: the line after the attribution text is not '$appendNsi'" }
$body = [Collections.Generic.List[string]]::new()
foreach ($line in $buildApp[($starts[0] + 1)..($end - 1)]) {
    $body.Add($line)
    if ($line.EndsWith('Details: data.md.')) {
        $body.Add('')
        $body.Add('The Windows programs (shanjie_tip.dll, shanjie-host.exe) and the installer''s installer-ca.dll statically link the Rust standard')
        $body.Add('library and the Rust crates windows, windows-core and windows-sys with the other windows-* crates they use, each under the')
        $body.Add('MIT license or the Apache License 2.0.')
    }
}
if (@($body | Where-Object { $_.EndsWith('Details: data.md.') }).Count -ne 1) { throw 'build-app.sh: expected one line ending "Details: data.md."' }
$attribution = ($body -join "`n") + "`n" + ([IO.File]::ReadAllText((Join-Path $Repo 'LICENSES\name-suggestion-index-BSD-3-Clause.txt')) -replace "`r`n", "`n")
if ($attribution -match 'Sparkle') { throw 'the attribution text names Sparkle, which the Windows package does not ship' }
[IO.File]::WriteAllText((Join-Path $gen 'CC-BY-SA-4.0-attribution.txt'), $attribution, [Text.UTF8Encoding]::new($false))

# ---- candle and light ----
$ext = @('-ext', 'WixUIExtension')
if ($Variant -eq 'T') { $ext += @('-ext', 'WixUtilExtension') }
$wixobj = Join-Path $work 'shanjie.wixobj'
Invoke-Checked $candle (@('-nologo', '-arch', 'x64') + $ext + @("-dVariant=$Variant", "-dVersion=$version", "-dRepo=$Repo", "-dBin=$binDir", "-dGen=$gen", '-out', $wixobj, $Source))
$lightArgs = @('-nologo') + $ext + @('-cultures:zh-TW', '-sice:ICE20')
if ($SkipValidation) { $lightArgs += '-sval' }
$lightOut = & $light @($lightArgs + @('-out', $msi, $wixobj)) 2>&1
$lightRc = $LASTEXITCODE
$lightOut | ForEach-Object { Write-Host $_ }
if ($lightRc -ne 0) { throw "light exited with $lightRc" }
# ICE61 (same-version upgrade) is expected. ICE63 would mean an action sits between InstallInitialize and RemoveExistingProducts.
if (($lightOut | Out-String) -match 'ICE63') { throw 'light reported ICE63' }
Write-Host ('{0}  {1}  (Variant {2}, version {3})' -f (Get-Sha256 $msi), [IO.Path]::GetFileName($msi), $Variant, $version)
