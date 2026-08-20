$ErrorActionPreference = "Stop"
$ok = $true
$target = if ($args.Count -gt 0) { $args[0] } else { $PSScriptRoot }
Get-ChildItem -Path $target -Filter "*.ps1" -Recurse | ForEach-Object {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors) | Out-Null
    if ($errors.Count -gt 0) {
        Write-Output ("ERR " + $_.Name + " : " + ($errors | ForEach-Object { $_.Message } | Out-String).Trim())
        $ok = $false
    }
    else {
        Write-Output ("OK  " + $_.Name)
    }
}
if (-not $ok) { exit 1 }
Write-Output "ALL PS1 PARSE OK"