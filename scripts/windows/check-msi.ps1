#Requires -Version 7
<#
Checks a built installer against the installer contract: decompiles it with WiX's dark.exe (-sras: every action with its sequence number;
-x: the installed files and the Binary table extracted) and reads that XML, then the extracted files with dumpbin. Prints every row it
reads, one line per finding, and exits 1 when anything differs. Every failure line starts with "FAIL [<area>]" so a broken copy can be
matched to the place it was broken (ci.yml's windows-msi job builds five broken copies and the test package T and expects each to fail).

  check-msi.ps1 -Msi <file.msi> [-Work <dir>]

The expected tables below are the ones of windows/installer/shanjie.wxs: change both together. Runs in PowerShell 7 on Windows with WiX 3
under %WIX% and Visual Studio's dumpbin.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Msi,
    [string]$Work
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Msi = (Resolve-Path $Msi).Path
if (-not $Work) { $Work = "$Msi.check" }
# dark writes the extraction folder into each Source as given: a relative -Work gave relative paths (first CI runs), so always pass a full one
$Work = [IO.Path]::GetFullPath($Work)
if (Test-Path -LiteralPath $Work) { Remove-Item -LiteralPath $Work -Recurse -Force }
New-Item -ItemType Directory -Force $Work | Out-Null

$script:failures = [Collections.Generic.List[string]]::new()
function Fail([string]$Area, [string]$Message) {
    $line = "FAIL [$Area] $Message"
    $script:failures.Add($line)
    Write-Host $line
}
function Section([string]$Title) { Write-Host ''; Write-Host "==== $Title" }

# ---- tools ----
if (-not $env:WIX) { throw 'WIX is not set' }
$dark = Join-Path $env:WIX 'bin\dark.exe'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$dumpbin = @(& $vswhere -latest -products '*' -find 'VC/Tools/MSVC/**/bin/Hostx64/x64/dumpbin.exe') | Select-Object -First 1
if (-not $dumpbin) { throw 'dumpbin.exe not found' }
function Invoke-Dumpbin([string]$Option, [string]$Path) {
    $o = & $dumpbin -nologo $Option $Path
    if ($LASTEXITCODE -ne 0) { throw "dumpbin $Option $Path exited with $LASTEXITCODE" }
    @($o | ForEach-Object { $_.TrimEnd("`r") })
}

# ---- decompile ----
$x = Join-Path $Work 'x'
$wxs = Join-Path $Work 'decompiled.wxs'
New-Item -ItemType Directory -Force $x | Out-Null
& $dark -nologo -sras -x $x $Msi $wxs
if ($LASTEXITCODE -ne 0) { throw "dark exited with $LASTEXITCODE" }
[xml]$doc = [IO.File]::ReadAllText($wxs)
$ns = [Xml.XmlNamespaceManager]::new($doc.NameTable)
$ns.AddNamespace('w', 'http://schemas.microsoft.com/wix/2006/wi')
function Nodes([string]$XPath, [Xml.XmlNode]$From = $doc) { @($From.SelectNodes($XPath, $ns)) }
function Attr([Xml.XmlNode]$Node, [string]$Name) { $a = $Node.Attributes[$Name]; if ($a) { $a.Value } else { $null } }
function Extracted([Xml.XmlNode]$Node) {
    $src = Attr $Node 'Source'
    if (-not $src) { $src = Attr $Node 'SourceFile' }
    if (-not $src) { return $null }
    # dark -x writes absolute paths into Source (first CI run); a relative SourceDir\ path is joined to the extraction folder
    if ([IO.Path]::IsPathRooted($src)) { return $src }
    Join-Path $x ($src -replace '^SourceDir\\', '')
}

Section 'CustomAction'
Nodes '//w:CustomAction' | ForEach-Object { Write-Host $_.OuterXml }
foreach ($seq in 'InstallExecuteSequence', 'InstallUISequence', 'AdminExecuteSequence', 'AdminUISequence', 'AdvertiseExecuteSequence') {
    Section $seq
    Nodes "//w:$seq/*" | Sort-Object { [int](Attr $_ 'Sequence') } | ForEach-Object { Write-Host $_.OuterXml }
}
Section 'Dialogs (id, then controls with their events and conditions)'
foreach ($d in Nodes '//w:Dialog') {
    Write-Host "Dialog $(Attr $d 'Id')"
    foreach ($c in Nodes 'w:Control' $d) {
        $inner = @(Nodes 'w:Publish|w:Condition' $c | ForEach-Object { $_.OuterXml }) -join ' '
        Write-Host "  Control $(Attr $c 'Id') Type=$(Attr $c 'Type') $inner"
    }
}
Nodes '//w:Publish[@Dialog]' | ForEach-Object { Write-Host $_.OuterXml }
Section 'Property'
Nodes '//w:Property' | ForEach-Object { Write-Host $_.OuterXml }
Section 'LaunchCondition'
Nodes '/w:Wix/w:Product/w:Condition' | ForEach-Object { Write-Host $_.OuterXml }
Section 'Registry'
Nodes '//w:RegistryKey|//w:RegistryValue|//w:RemoveRegistryKey|//w:RemoveRegistryValue' | ForEach-Object { Write-Host $_.OuterXml }
Section 'Binary'
Nodes '//w:Binary' | ForEach-Object { Write-Host $_.OuterXml }
Section 'Upgrade'
Nodes '//w:Upgrade' | ForEach-Object { Write-Host $_.OuterXml }
Section 'Files (id, install path, extracted as)'

# ---- the install folder and the files ----
$installDir = @(Nodes "//w:Directory[@Id='ProgramFiles64Folder']/w:Directory[@Name='Shanjie']")
$installId = $null
if ($installDir.Count -ne 1) { Fail 'Directory' "expected one folder Shanjie under ProgramFiles64Folder, found $($installDir.Count)" }
else {
    $installId = Attr $installDir[0] 'Id'
    if ($installId -cnotmatch '[a-z]') { Fail 'Directory' "the install folder's id $installId has no lower-case letter (a public property can be set from the command line)" }
}
$expectedFiles = @(
    'shanjie_tip.dll', 'shanjie-host.exe', 'LICENSE',
    'data\lexicon\mcbpmf-data.txt', 'data\lexicon\overlay-add.tsv', 'data\lexicon\sandhi-add.tsv', 'data\lexicon\names-add.tsv', 'data\lexicon\demote.tsv',
    'data\lexicon\variant-order.tsv', 'data\lexicon\std-exempt.tsv', 'data\lexicon\rerank-protect.tsv', 'data\lexicon\digits.tsv', 'data\lexicon\mixed-words.tsv',
    'data\packs\acg-add.tsv', 'data\packs\acg-sources.tsv', 'data\packs\acg.json', 'data\packs\it-add.tsv', 'data\packs\it-sources.tsv', 'data\packs\it.json',
    'data\lm\bigram.sjlm', 'data\lm\classes.sjc', 'data\lm\kn.sjkn', 'data\lm\trigram.sjt3', 'data\lm\trigram-chat.sjt3', 'data\lm\tinylm.sjtl', 'data\lm\colloquial.sjlm',
    'LICENSES\McBopomofo-MIT.txt', 'LICENSES\data.md', 'LICENSES\name-suggestion-index-BSD-3-Clause.txt', 'LICENSES\colloquial-attribution.tsv',
    'LICENSES\CC-BY-SA-4.0-attribution.txt'
)
$files = @{}
foreach ($f in Nodes '//w:File') {
    $dirs = [Collections.Generic.List[Xml.XmlNode]]::new()
    for ($p = $f.ParentNode; $p -is [Xml.XmlElement]; $p = $p.ParentNode) { if ($p.LocalName -eq 'Directory') { $dirs.Insert(0, $p) } }
    $at = -1
    for ($i = 0; $i -lt $dirs.Count; $i++) { if ($installId -and (Attr $dirs[$i] 'Id') -ceq $installId) { $at = $i } }
    $name = Attr $f 'Name'
    if ($at -lt 0) { Fail 'Files' "file $(Attr $f 'Id') ($name) is outside the install folder"; continue }
    $rel = (@($dirs | Select-Object -Skip ($at + 1) | ForEach-Object { Attr $_ 'Name' }) + $name) -join '\'
    $path = Extracted $f
    Write-Host "$(Attr $f 'Id')  $rel  <-  $path"
    if ($files.ContainsKey($rel)) { Fail 'Files' "$rel is listed twice" }
    $files[$rel] = $path
    if ($null -ne (Attr $f 'SelfRegCost')) { Fail 'SelfReg' "$rel has SelfRegCost" }
}
foreach ($e in $expectedFiles) { if (-not $files.ContainsKey($e)) { Fail 'Files' "missing $e" } }
foreach ($k in $files.Keys) { if ($expectedFiles -notcontains $k) { Fail 'Files' "not in the list: $k" } }
foreach ($k in @($files.Keys)) { if (-not $files[$k] -or -not (Test-Path -LiteralPath $files[$k] -PathType Leaf)) { Fail 'Files' "$k was not extracted"; $files.Remove($k) } }

# The models and the colloquial attribution list equal data/*.sha256.
$hashFiles = @{
    'data\lm\bigram.sjlm' = 'bigram.sjlm'; 'data\lm\classes.sjc' = 'classes.sjc'; 'data\lm\kn.sjkn' = 'kn.sjkn'; 'data\lm\trigram.sjt3' = 'trigram.sjt3'
    'data\lm\trigram-chat.sjt3' = 'trigram-chat.sjt3'; 'data\lm\tinylm.sjtl' = 'tinylm.sjtl'; 'data\lm\colloquial.sjlm' = 'colloquial.sjlm'
    'LICENSES\colloquial-attribution.tsv' = 'colloquial-attribution.tsv'
}
Section 'Model hashes'
foreach ($rel in $hashFiles.Keys) {
    $want = ([IO.File]::ReadAllText((Join-Path $Repo "data\$($hashFiles[$rel]).sha256")).Trim() -split '\s+')[0].ToLowerInvariant()
    if (-not $files.ContainsKey($rel)) { continue }
    $got = (Get-FileHash -Algorithm SHA256 -LiteralPath $files[$rel]).Hash.ToLowerInvariant()
    Write-Host "$got  $rel (want $want)"
    if ($got -ne $want) { Fail 'Files' "$rel has SHA-256 $got, data/$($hashFiles[$rel]).sha256 says $want" }
}

# The attribution file's first paragraph names exactly these files (as scripts/build-app.sh writes them).
$attributed = @('overlay-add.tsv', 'names-add.tsv', 'packs/acg-add.tsv', 'packs/acg-sources.tsv', 'packs/acg.json', 'packs/it-add.tsv', 'packs/it-sources.tsv', 'packs/it.json',
    'demote.tsv', 'std-exempt.tsv', 'digits.tsv', 'bigram.sjlm', 'classes.sjc', 'kn.sjkn', 'trigram.sjt3', 'trigram-chat.sjt3', 'tinylm.sjtl', 'colloquial.sjlm')
Section 'CC-BY-SA-4.0-attribution.txt'
if ($files.ContainsKey('LICENSES\CC-BY-SA-4.0-attribution.txt')) {
    $text = [IO.File]::ReadAllText($files['LICENSES\CC-BY-SA-4.0-attribution.txt']) -replace "`r`n", "`n"
    $i = $text.IndexOf('are licensed')
    if ($i -lt 0) { Fail 'Attribution' 'no "are licensed" in the attribution file' }
    else {
        $named = @(($text.Substring(0, $i) -replace '\s+', ' ') -split ',| and ' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        Write-Host ($named -join ' | ')
        foreach ($n in $attributed) { if ($named -cnotcontains $n) { Fail 'Attribution' "the first paragraph does not name $n" } }
        foreach ($n in $named) { if ($attributed -cnotcontains $n) { Fail 'Attribution' "the first paragraph names $n, which is not in the list" } }
        if ($named.Count -ne $attributed.Count) { Fail 'Attribution' "the first paragraph names $($named.Count) files, the list has $($attributed.Count)" }
    }
}

# ---- the binaries ----
Section 'shanjie_tip.dll and shanjie-host.exe'
foreach ($rel in 'shanjie_tip.dll', 'shanjie-host.exe') {
    if (-not $files.ContainsKey($rel)) { continue }
    $deps = Invoke-Dumpbin '/dependents' $files[$rel]
    $deps | ForEach-Object { Write-Host "  $_" }
    if (-not ($deps -match '(?i)^\s+kernel32\.dll$')) { Fail 'Binaries' "$rel`: no import list read" }
    if ($deps -match '(?i)vcruntime|msvcp|ucrtbase|api-ms-win-crt') { Fail 'Binaries' "$rel imports a C runtime DLL" }
    $exports = Invoke-Dumpbin '/exports' $files[$rel]
    if (-not ($exports -match '\sSjMarkReleaseIds$')) { Fail 'Binaries' "$rel has no SjMarkReleaseIds export (not a release-ids build)" }
    if ($exports -match '\sSjMarkTiming$') { Fail 'Binaries' "$rel exports SjMarkTiming (a timing build)" }
}
if ($files.ContainsKey('shanjie-host.exe')) {
    $headers = Invoke-Dumpbin '/headers' $files['shanjie-host.exe']
    $sub = @($headers -match 'subsystem \(')
    Write-Host "  shanjie-host.exe: $($sub -join '; ')"
    if (-not ($headers -match 'subsystem \(Windows GUI\)')) { Fail 'Binaries' 'shanjie-host.exe is not a windows-subsystem program' }
}

# installer-ca.dll: the only DLL in the Binary table; its static imports in the list; no delay-load; the three exports.
Section 'Binary table and installer-ca.dll'
$allowedImports = @('msi.dll', 'kernel32.dll', 'user32.dll', 'advapi32.dll', 'imm32.dll', 'ntdll.dll')
$caPath = $null
foreach ($b in Nodes '//w:Binary') {
    $id = Attr $b 'Id'
    $path = Extracted $b
    if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { Fail 'Binary' "$id was not extracted"; continue }
    $head = [byte[]]::new(2)
    $fs = [IO.File]::OpenRead($path); try { $null = $fs.Read($head, 0, 2) } finally { $fs.Dispose() }
    $pe = $head[0] -eq 0x4D -and $head[1] -eq 0x5A
    Write-Host "$id  PE=$pe  $path"
    if ($pe -and $id -cne 'InstallerCa') { Fail 'Binary' "$id is a program (MZ); the only custom action DLL may be InstallerCa" }
    if ($id -ceq 'InstallerCa') { $caPath = $path }
}
if (-not $caPath) { Fail 'Binary' 'no InstallerCa' }
else {
    $deps = Invoke-Dumpbin '/dependents' $caPath
    $deps | ForEach-Object { Write-Host "  $_" }
    $dlls = @($deps | Where-Object { $_ -match '(?i)^\s+\S+\.dll$' } | ForEach-Object { $_.Trim().ToLowerInvariant() })
    if ($dlls.Count -eq 0) { Fail 'Binary' 'installer-ca.dll: no import list read' }
    foreach ($d in $dlls) { if ($allowedImports -notcontains $d -and $d -notmatch '^api-ms-win-') { Fail 'Binary' "installer-ca.dll imports $d, which is not in the list" } }
    if ($deps -match '(?i)delay load') { Fail 'Binary' 'installer-ca.dll has delay-loaded imports' }
    $exports = Invoke-Dumpbin '/exports' $caPath
    foreach ($e in 'AskKeep', 'KillHost', 'DeleteSettings') { if (-not ($exports -match "\s$e$")) { Fail 'Binary' "installer-ca.dll does not export $e" } }
}
foreach ($ca in Nodes '//w:CustomAction[@BinaryKey]') {
    if ((Attr $ca 'BinaryKey') -cne 'InstallerCa') { Fail "CustomAction $(Attr $ca 'Id')" "runs from Binary $(Attr $ca 'BinaryKey')" }
}

# ---- custom actions: exactly these rows ----
# Attributes with their default value are dropped on both sides before comparing (dark may or may not write a default).
$defaults = @{ Execute = 'immediate'; Return = 'check'; Impersonate = 'yes'; TerminalServerAware = 'no'; HideTarget = 'no' }
$regsvr = '"[System64Folder]regsvr32.exe"'
$expectedCa = [ordered]@{
    AskKeep            = @{ BinaryKey = 'InstallerCa'; DllEntry = 'AskKeep' }
    SetKEEPSETTINGS    = @{ Property = 'KEEPSETTINGS'; Value = '1' }
    SetKEEPMEMORY      = @{ Property = 'KEEPMEMORY'; Value = '1' }
    UnregisterRollback = @{ Directory = $installId; ExeCommand = "$regsvr /s `"[#FTipDll]`""; Execute = 'rollback'; Impersonate = 'no'; Return = 'ignore' }
    Unregister         = @{ Directory = $installId; ExeCommand = "$regsvr /s /u `"[#FTipDll]`""; Execute = 'deferred'; Impersonate = 'no'; Return = 'ignore' }
    SetKillHost        = @{ Property = 'KillHost'; Value = '[#FHost]' }
    KillHost           = @{ BinaryKey = 'InstallerCa'; DllEntry = 'KillHost'; Execute = 'deferred'; Impersonate = 'no'; Return = 'ignore' }
    RegisterRollback   = @{ Directory = $installId; ExeCommand = "$regsvr /s /u `"[#FTipDll]`""; Execute = 'rollback'; Impersonate = 'no'; Return = 'ignore' }
    Register           = @{ Directory = $installId; ExeCommand = "$regsvr /s `"[#FTipDll]`""; Execute = 'deferred'; Impersonate = 'no'; Return = 'check' }
    DeleteSettings     = @{ BinaryKey = 'InstallerCa'; DllEntry = 'DeleteSettings'; Execute = 'deferred'; Impersonate = 'yes'; TerminalServerAware = 'yes'; Return = 'ignore' }
    StartHost          = @{ Directory = $installId; ExeCommand = '"[#FHost]" --resident --logon'; Return = 'asyncNoWait' }
}
function Normalised([hashtable]$h) {
    $o = @{}
    foreach ($k in $h.Keys) { if (-not ($defaults.ContainsKey($k) -and $defaults[$k] -ceq $h[$k])) { $o[$k] = $h[$k] } }
    $o
}
$actual = @{}
foreach ($ca in Nodes '//w:CustomAction') {
    $h = @{}
    foreach ($a in $ca.Attributes) { if ($a.Name -ne 'Id') { $h[$a.Name] = $a.Value } }
    $actual[(Attr $ca 'Id')] = Normalised $h
}
foreach ($id in $expectedCa.Keys) {
    if (-not $actual.ContainsKey($id)) { Fail "CustomAction $id" 'missing'; continue }
    $want = Normalised $expectedCa[$id]
    $got = $actual[$id]
    foreach ($k in @($want.Keys) + @($got.Keys) | Sort-Object -Unique) {
        $wv = if ($want.ContainsKey($k)) { $want[$k] } else { "(default $($defaults[$k]))" }
        $gv = if ($got.ContainsKey($k)) { $got[$k] } else { "(default $($defaults[$k]))" }
        if ($wv -cne $gv) { Fail "CustomAction $id" "$k is $gv, expected $wv" }
    }
}
foreach ($id in $actual.Keys) { if (-not $expectedCa.Contains($id)) { Fail "CustomAction $id" 'not in the table (unexpected custom action)' } }

# ---- sequences ----
function Entries([string]$Seq) {
    foreach ($n in Nodes "//w:$Seq/*") {
        $name = switch ($n.LocalName) { 'Custom' { Attr $n 'Action' } 'Show' { Attr $n 'Dialog' } default { $n.LocalName } }
        [pscustomobject]@{ Name = $name; Custom = ($n.LocalName -eq 'Custom'); Sequence = (Attr $n 'Sequence'); Condition = $n.InnerText.Trim() }
    }
}
foreach ($seq in 'InstallUISequence', 'AdminExecuteSequence', 'AdminUISequence', 'AdvertiseExecuteSequence') {
    foreach ($e in @(Entries $seq | Where-Object Custom)) { Fail $seq "has the custom action $($e.Name); custom actions are only in InstallExecuteSequence (and StartHost on the exit dialog)" }
}
$exec = @(Entries 'InstallExecuteSequence')
foreach ($e in $exec) { if (-not $e.Sequence) { Fail 'InstallExecuteSequence' "$($e.Name) has no sequence number" } }
$exec = @($exec | Where-Object Sequence | Sort-Object { [int]$_.Sequence })
$expectedConditions = [ordered]@{
    AskKeep            = 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE AND (UILevel=3 OR UILevel=4) AND KEEPSETTINGS="" AND KEEPMEMORY=""'
    SetKEEPSETTINGS    = 'KEEPSETTINGS<>"0"'
    SetKEEPMEMORY      = 'KEEPMEMORY<>"0"'
    UnregisterRollback = 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE'
    Unregister         = 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE'
    SetKillHost        = 'REMOVE="ALL"'
    KillHost           = 'REMOVE="ALL"'
    RegisterRollback   = 'NOT Installed AND NOT WIX_UPGRADE_DETECTED'
    Register           = 'NOT REMOVE="ALL"'
    DeleteSettings     = 'REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE AND KEEPSETTINGS="0"'
}
$customs = @($exec | Where-Object Custom)
foreach ($id in $expectedConditions.Keys) {
    $rows = @($customs | Where-Object Name -ceq $id)
    if ($rows.Count -ne 1) { Fail "InstallExecuteSequence $id" "scheduled $($rows.Count) times, expected once"; continue }
    if ($rows[0].Condition -cne $expectedConditions[$id]) { Fail "InstallExecuteSequence $id" "condition is '$($rows[0].Condition)', expected '$($expectedConditions[$id])'" }
}
foreach ($c in $customs) { if (-not $expectedConditions.Contains($c.Name)) { Fail "InstallExecuteSequence $($c.Name)" 'not in the table (unexpected scheduled action)' } }
# Positions: each chain is consecutive in the sequence.
$order = @($exec | ForEach-Object Name)
foreach ($chain in @(
        @('InstallValidate', 'AskKeep', 'SetKEEPSETTINGS', 'SetKEEPMEMORY'),
        @('UnregisterRollback', 'Unregister', 'SetKillHost', 'KillHost', 'RemoveFiles'),
        @('InstallFiles', 'RegisterRollback', 'Register'),
        @('DeleteSettings', 'InstallFinalize'))) {
    $i = [array]::IndexOf($order, $chain[0])
    for ($k = 1; $k -lt $chain.Count; $k++) {
        $j = [array]::IndexOf($order, $chain[$k])
        if ($i -lt 0 -or $j -ne $i + $k) { Fail "InstallExecuteSequence $($chain[$k])" "not right after $($chain[$k - 1]) (expected the run $($chain -join ' > '))" }
    }
}

# ---- the one action a dialog starts ----
$doActions = @(Nodes '//w:Publish[@Event="DoAction"]')
foreach ($p in $doActions) {
    $dialog = Attr $p 'Dialog'; if (-not $dialog -and $p.ParentNode.ParentNode.LocalName -eq 'Dialog') { $dialog = Attr $p.ParentNode.ParentNode 'Id' }
    $control = Attr $p 'Control'; if (-not $control -and $p.ParentNode.LocalName -eq 'Control') { $control = Attr $p.ParentNode 'Id' }
    $where = "$dialog/$control DoAction $(Attr $p 'Value') condition '$($p.InnerText.Trim())'"
    Write-Host "DoAction: $where"
    if ($dialog -cne 'ExitDialog' -or $control -cne 'Finish' -or (Attr $p 'Value') -cne 'StartHost') { Fail 'DoAction' "unexpected: $where" }
    elseif ($p.InnerText.Trim() -cne 'NOT Installed') { Fail 'DoAction' "ExitDialog/Finish StartHost has condition '$($p.InnerText.Trim())', expected 'NOT Installed'" }
}
if ($doActions.Count -ne 1) { Fail 'DoAction' "$($doActions.Count) DoAction events, expected exactly one (ExitDialog/Finish StartHost)" }

# ---- dialogs ----
$dialogIds = @(Nodes '//w:Dialog' | ForEach-Object { Attr $_ 'Id' })
foreach ($d in 'LicenseAgreementDlg', 'WelcomeEulaDlg', 'InstallDirDlg', 'BrowseDlg', 'InvalidDirDlg', 'CustomizeDlg', 'SetupTypeDlg', 'FeaturesDlg', 'FilesInUse', 'MsiRMFilesInUse', 'MaintenanceTypeDlg') {
    if ($dialogIds -ccontains $d) { Fail 'Dialogs' "has $d" }
}
foreach ($c in Nodes '//w:Control') {
    if (@('SelectionTree', 'DirectoryCombo', 'DirectoryList', 'PathEdit', 'ScrollableText') -contains (Attr $c 'Type')) {
        Fail 'Dialogs' "control $(Attr $c.ParentNode 'Id')/$(Attr $c 'Id') is a $(Attr $c 'Type') (feature tree, folder browsing or licence text)"
    }
}
# The exit page's sentence: shown by WiX's OptionalText control only when NOT Installed.
$finishText = '善解已經裝好。已經開著的 App 切一次輸入法就能用。'
$optional = @(Nodes "//w:Dialog[@Id='ExitDialog']/w:Control[@Id='OptionalText']")
if ($optional.Count -ne 1) { Fail 'Dialogs' 'ExitDialog has no OptionalText control' }
elseif (-not (@(Nodes "w:Condition[@Action='show']" $optional[0]) | Where-Object { $_.InnerText -match '\bNOT Installed\b' })) { Fail 'Dialogs' 'ExitDialog/OptionalText is not shown only when NOT Installed' }

# ---- no COM classes, file associations, self-registration, shortcuts; no files or folders made or removed outside the install ----
foreach ($t in 'Class', 'Extension', 'Shortcut', 'RemoveFile', 'RemoveFolder', 'CreateFolder') {
    $n = @(Nodes "//w:$t")
    if ($n.Count -gt 0) { Fail $t "$($n.Count) $t element(s): $($n[0].OuterXml)" }
}

# ---- registry: only the Run value ----
$values = @(Nodes '//w:RegistryValue')
foreach ($v in $values) {
    $root = Attr $v 'Root'; $key = Attr $v 'Key'
    if ($v.ParentNode.LocalName -eq 'RegistryKey') {
        if (-not $root) { $root = Attr $v.ParentNode 'Root' }
        $pk = Attr $v.ParentNode 'Key'; if ($pk) { $key = if ($key) { "$pk\$key" } else { $pk } }
    }
    $desc = "Root=$root Key=$key Name=$(Attr $v 'Name') Type=$(Attr $v 'Type') Value=$(Attr $v 'Value')"
    if ($root -cne 'HKLM' -or $key -cne 'Software\Microsoft\Windows\CurrentVersion\Run' -or (Attr $v 'Name') -cne 'Shanjie' -or
        (Attr $v 'Value') -cne "`"[$installId]shanjie-host.exe`" --resident --logon" -or (Attr $v 'Type') -cne 'string') {
        Fail 'Registry' "unexpected row: $desc"
    }
}
if ($values.Count -ne 1) { Fail 'Registry' "$($values.Count) registry values, expected exactly the Run value" }
foreach ($t in 'RemoveRegistryKey', 'RemoveRegistryValue') { foreach ($n in Nodes "//w:$t") { Fail 'Registry' "unexpected $t`: $($n.OuterXml)" } }
foreach ($k in Nodes '//w:RegistryKey') { if (@(Nodes 'w:RegistryValue' $k).Count -eq 0) { Fail 'Registry' "a key row with no value: $($k.OuterXml)" } }

# ---- properties ----
function PropertyValue([string]$Id) {
    $n = @(Nodes "//w:Property[@Id='$Id']")
    if ($n.Count -eq 0) { return $null }
    $v = Attr $n[0] 'Value'
    if ($null -eq $v -and $n[0].InnerText) { $v = $n[0].InnerText }
    $v
}
if ((PropertyValue 'MSIRESTARTMANAGERCONTROL') -cne 'Disable') { Fail 'Property' 'MSIRESTARTMANAGERCONTROL is not Disable' }
if ((PropertyValue 'REBOOT') -cne 'ReallySuppress') { Fail 'Property' 'REBOOT is not ReallySuppress' }
foreach ($p in 'KEEPSETTINGS', 'KEEPMEMORY') { $v = PropertyValue $p; if ($v) { Fail 'Property' "$p has a value ($v): it must stay undefined unless given" } }
if ((PropertyValue 'WIXUI_EXITDIALOGOPTIONALTEXT') -cne $finishText) { Fail 'Property' "WIXUI_EXITDIALOGOPTIONALTEXT is not the exit page's sentence" }
$secure = [Collections.Generic.HashSet[string]]::new()
foreach ($n in Nodes '//w:Property[@Secure="yes"]') { $null = $secure.Add((Attr $n 'Id')) }
$scp = PropertyValue 'SecureCustomProperties'
if ($scp) { foreach ($s in $scp -split ';') { if ($s) { $null = $secure.Add($s) } } }
$upgradeProps = @(Nodes '//w:UpgradeVersion' | ForEach-Object { Attr $_ 'Property' } | Where-Object { $_ })
# MajorUpgrade's two action properties; the first CI run's decompiled source showed them as secure properties and no UpgradeVersion element
$allowedSecure = @('KEEPSETTINGS', 'KEEPMEMORY', 'WIX_UPGRADE_DETECTED', 'WIX_DOWNGRADE_DETECTED') + $upgradeProps
Write-Host "Secure: $(@($secure) -join ';')  (upgrade properties: $($upgradeProps -join ';'))"
foreach ($s in $secure) { if ($allowedSecure -cnotcontains $s) { Fail 'Property' "$s is secure (only the two KEEP properties and the upgrade properties may be)" } }
foreach ($s in 'KEEPSETTINGS', 'KEEPMEMORY') { if (-not $secure.Contains($s)) { Fail 'Property' "$s is not secure" } }

# ---- launch condition: no per-user install ----
$lc = @(Nodes '/w:Wix/w:Product/w:Condition' | Where-Object { $_.InnerText.Trim() -ceq 'Installed OR MSIINSTALLPERUSER<>1' })
if ($lc.Count -ne 1) { Fail 'LaunchCondition' "no launch condition 'Installed OR MSIINSTALLPERUSER<>1'" }

Write-Host ''
if ($script:failures.Count -gt 0) {
    Write-Host "check-msi: $($script:failures.Count) failure(s) in $Msi"
    exit 1
}
Write-Host "check-msi: $Msi matches the contract"
