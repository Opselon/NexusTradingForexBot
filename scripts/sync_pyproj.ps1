# ============================================================
# Nexus Scalp Engine - Visual Studio project file sync [Windows]
# ============================================================
# Regenerates NexusTradingForexBot.pyproj from the CURRENT tracked
# tree (git ls-files) with the proven CURATED scope: main structure
# + Python sources only. Self-verifying and idempotent:
#   * skips the write when the generated content is byte-identical
#     to the file on disk (exit 0, no diff, no churn for agents)
#   * writes only when content actually changed (exit 1)
#   * aborts without touching the file on ANY verification failure
# Provenance: repo commit f0f391f ("Sync pyproj to current tree -
# curated scope") regenerated the same inventory with an equivalent
# Python script; this is the durable in-repo PowerShell port.
# Scope ruling: a full-tree pyproj (1818 entries incl. scratch/ +
# docs/forensic-docs + Web/vendor) crashed Visual Studio on load;
# user directive keeps ONLY important files + Python = main structure.
# ============================================================
[CmdletBinding()]
param(
    # Dry run: validate the generated XML + paths, write nothing, print a report.
    [switch]$VerifyOnly,
    # Also write the generated file to this path for diffing (repo file untouched).
    [string]$OutFile
)

$ErrorActionPreference = "Stop"

# --- locate the repo root from this script's own position ---------------
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PyprojPath = Join-Path $RepoRoot "NexusTradingForexBot.pyproj"
if (-not (Test-Path $PyprojPath)) {
    Write-Error "NexusTradingForexBot.pyproj not found under $RepoRoot (wrong repo?)"
    exit 2
}

Push-Location $RepoRoot
try {
    # --- 1. tracked file inventory ----------------------------------------
    # Plain newline-delimited ls-files (repo verified 0 non-ascii paths, so
    # quoting never triggers); avoids PowerShell NUL-handling quirks of -z.
    $raw = & git -c core.quotepath=false ls-files
    if ($LASTEXITCODE -ne 0) { Write-Host "FATAL: git ls-files failed" -ForegroundColor Red; exit 2 }
    $tracked = @($raw | Where-Object { $_ -and ($_ -ne "") })
    Write-Host ("tracked files: {0}" -f $tracked.Count)

    # --- 2. curated scope filter (IDENTICAL to the f0f391f regeneration) --
    $excludedTops = @(
        "scratch", "artifacts", "pics", "release", "logs", "data", "archive",
        "node_modules", "ci-results", "_backup_portable_20260822_1316_bad_cli"
    )
    $docsSubsets = @("architecture")

    function Test-KeepPath([string]$relPath) {
        $p = $relPath -replace "/", "\"
        $top = ($p -split "\\")[0]
        if ($excludedTops -contains $top) { return $false }
        switch -Wildcard ($top) {
            "src" {
                $name = $p -split "\\" | Select-Object -Last 1
                return ($p -like "*.py") -or ($name -eq "py.typed")
            }
            { $_ -in @("tests", "scripts") } {
                return ($p -like "*.py") -or ($p -eq "tests\critical_suite.txt")
            }
            "docs" {
                $rest = $p.Substring("docs\".Length)
                if (-not $rest.Contains("\")) { return $true }   # top-level docs
                return ($docsSubsets -contains ($rest -split "\\")[0])
            }
            { $_ -in @("agents", "Agent", "Web", "configs", "docker", "installer", ".github") } {
                return $true
            }
            default { return (-not $p.Contains("\")) }           # root files only
        }
    }

    # EXACT emulation of the proven generator's sort (Python
    # sorted(paths, key=str.lower)): ordinal compare of the LOWERCASED path,
    # with git's original ordinal order preserved for equal-lowercase ties
    # (Python's sort is stable). Culture-aware comparers cannot reproduce
    # this ('_' vs digit weights differ), so sort on a composite key =
    # lowerpath + SOH + original-index; keys are unique, making the result
    # deterministic and byte-identical to the Python output.
    $keptList = [string[]]@($tracked | Where-Object { Test-KeepPath $_ })
    $n = $keptList.Count
    $keys = New-Object 'string[]' $n
    $items = New-Object 'string[]' $n
    for ($idx = 0; $idx -lt $n; $idx++) {
        $keys[$idx] = $keptList[$idx].ToLowerInvariant() + [string][char]1 + $idx.ToString('D10')
        $items[$idx] = $keptList[$idx]
    }
    [System.Array]::Sort($keys, $items, [System.StringComparer]::Ordinal)
    $kept = $items
    # git reports forward slashes; the MSBuild convention (and the committed
    # pyproj) uses backslashes - normalize before emitting.
    $kept = @($kept | ForEach-Object { $_ -replace "/", "\" })
    $pyFiles = @($kept | Where-Object { $_ -like "*.py" })
    $contentFiles = @($kept | Where-Object { $_ -notlike "*.py" })
    Write-Host ("kept: {0} (Compile {1} / Content {2})  dropped by scope: {3}" -f `
        $kept.Count, $pyFiles.Count, $contentFiles.Count, ($tracked.Count - $kept.Count))

    # --- 3. folder set (all ancestors of kept files) ----------------------
    $folderSet = New-Object System.Collections.Generic.HashSet[string]
    foreach ($f in $kept) {
        $d = $f -replace "/", "\"
        while ($true) {
            $i = $d.LastIndexOf("\")
            if ($i -le 0) { break }
            $d = $d.Substring(0, $i)
            [void]$folderSet.Add($d)
        }
    }
    $folders = @($folderSet | Sort-Object `
        -Property @{ Expression = { $_.Split("\").Count } }, @{ Expression = { $_.ToLower() } })
    Write-Host ("folders: {0}" -f $folders.Count)

    # --- 4. emit MSBuild XML (CRLF, UTF-8, no BOM) ------------------------
    # Header/footer mirror the committed pyproj: same ProjectGuid,
    # StartupFile, InterpreterId (.venv), PythonTools targets import.
    $header = @'
<?xml version="1.0" encoding="utf-8"?>
<Project DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003" ToolsVersion="4.0">
  <PropertyGroup>
    <Configuration Condition=" '$(Configuration)' == '' ">Debug</Configuration>
    <SchemaVersion>2.0</SchemaVersion>
    <ProjectGuid>40871851-2619-4c46-8088-e7bf0793c0f3</ProjectGuid>
    <ProjectHome>.</ProjectHome>
    <StartupFile>NexusTradingForexBot.py</StartupFile>
    <SearchPath>
    </SearchPath>
    <WorkingDirectory>.</WorkingDirectory>
    <OutputPath>.</OutputPath>
    <Name>NexusTradingForexBot</Name>
    <RootNamespace>NexusTradingForexBot</RootNamespace>
    <InterpreterId>Global|VisualStudio|.venv</InterpreterId>
  </PropertyGroup>
  <PropertyGroup Condition=" '$(Configuration)' == 'Debug' ">
    <DebugSymbols>true</DebugSymbols>
    <EnableUnmanagedDebugging>false</EnableUnmanagedDebugging>
  </PropertyGroup>
  <PropertyGroup Condition=" '$(Configuration)' == 'Release' ">
    <DebugSymbols>true</DebugSymbols>
    <EnableUnmanagedDebugging>false</EnableUnmanagedDebugging>
  </PropertyGroup>
'@
    $footer = @'
  <ItemGroup>
    <InterpreterReference Include="Global|VisualStudio|.venv" />
  </ItemGroup>
  <Import Project="$(MSBuildExtensionsPath32)\Microsoft\VisualStudio\v$(VisualStudioVersion)\Python Tools\Microsoft.PythonTools.targets" />
  <!-- Uncomment the CoreCompile target to enable the Build command in
       Visual Studio and specify your pre- and post-build commands in
       the BeforeBuild and AfterBuild targets below. -->
  <!--<Target Name="CoreCompile" />-->
  <Target Name="BeforeBuild">
  </Target>
  <Target Name="AfterBuild">
  </Target>
</Project>
'@
    $header = $header.TrimEnd("`r", "`n")
    $footer = $footer.TrimEnd("`r", "`n")

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($header)
    $lines.Add("  <ItemGroup>")
    foreach ($p in $pyFiles)      { $lines.Add(('    <Compile Include="{0}" />' -f $p)) }
    $lines.Add("  </ItemGroup>")
    $lines.Add("  <ItemGroup>")
    foreach ($p in $folders)      { $lines.Add(('    <Folder Include="{0}\" />' -f $p)) }
    $lines.Add("  </ItemGroup>")
    $lines.Add("  <ItemGroup>")
    foreach ($p in $contentFiles) { $lines.Add(('    <Content Include="{0}" />' -f $p)) }
    $lines.Add("  </ItemGroup>")
    $lines.Add($footer)
    # Normalize to uniform CRLF regardless of this script file's own EOL style
    # (here-strings inherit the source encoding; the committed pyproj is CRLF).
    $newText = (($lines -join "`r`n") + "`r`n") -replace "`r`n", "`n" -replace "`n", "`r`n"

    # --- 5. verification gates (fail -> no write at all) ------------------
    $tmp = Join-Path $env:TEMP ("pyproj_sync_{0}.xml" -f $PID)
    [System.IO.File]::WriteAllText($tmp, $newText, (New-Object System.Text.UTF8Encoding($false)))

    $xml = New-Object System.Xml.XmlDocument
    $xml.Load($tmp)   # throws if malformed
    $includes = New-Object System.Collections.Generic.List[string]
    foreach ($el in $xml.SelectNodes("//*[@Include]")) {
        [void]$includes.Add($el.GetAttribute("Include"))
    }
    if (($includes | Sort-Object -Unique).Count -ne $includes.Count) {
        Write-Host "FATAL: duplicate Include paths detected" -ForegroundColor Red; exit 3
    }
    # Path existence: real entries only (skip InterpreterReference, which is
    # an interpreter id, and Folder entries, which carry a trailing slash).
    $missing = @($xml.SelectNodes("//*[@Include]") | Where-Object {
        ($_.LocalName -ne "InterpreterReference") -and
        (-not $_.GetAttribute("Include").EndsWith("\")) -and
        (-not (Test-Path (Join-Path $RepoRoot $_.GetAttribute("Include"))))
    })
    if ($missing.Count -gt 0) {
        $sample = @($missing | Select-Object -First 5 | ForEach-Object { $_.GetAttribute("Include") }) -join "; "
        Write-Host ("FATAL: {0} Include paths missing on disk, e.g.: {1}" -f $missing.Count, $sample) -ForegroundColor Red
        exit 3
    }
    Write-Host ("verified: {0} includes, 0 duplicates, 0 missing on disk" -f $includes.Count)

    # --- 6. outputs (idempotent skip / diff report) -----------------------
    $current = [System.IO.File]::ReadAllBytes($PyprojPath)
    $newBytes = [System.IO.File]::ReadAllBytes($tmp)

    if ($PSBoundParameters.ContainsKey("OutFile")) {
        [System.IO.File]::WriteAllBytes($OutFile, $newBytes)
        Write-Host "generated copy written to: $OutFile"
    }

    $identical = ($current.Length -eq $newBytes.Length) -and `
        [System.Linq.Enumerable]::SequenceEqual([byte[]]$current, [byte[]]$newBytes)
    if ($identical) {
        Write-Host "pyproj is ALREADY in sync - no write needed (idempotent)." -ForegroundColor Green
        exit 0
    }

    if ($VerifyOnly) {
        Write-Host "VERIFYONLY: pyproj is OUT OF SYNC (script would rewrite it)." -ForegroundColor Yellow
        Write-Host ("size: current {0} bytes vs generated {1} bytes" -f $current.Length, $newBytes.Length)
        exit 4
    }

    [System.IO.File]::WriteAllBytes($PyprojPath, $newBytes)
    $added = 0; $removed = 0
    Write-Host ("pyproj rewritten: {0} bytes (was {1} bytes)." -f $newBytes.Length, $current.Length) -ForegroundColor Green
    Write-Host "Next: git diff --stat -- NexusTradingForexBot.pyproj to review; commit with <AGENT>: <summary>."
    exit 1
}
finally {
    Pop-Location
}
