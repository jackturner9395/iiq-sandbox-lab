<#
  Loads the sandbox demo data into the local IIQ 8.4 sandbox.
  Run:  powershell -ExecutionPolicy Bypass -File .\setup\load-sandbox-data.ps1
  Safe to re-run (imports overwrite, aggregations update).
#>
param(
  [string]$Repo = (Split-Path $PSScriptRoot -Parent),
  [string]$Root = "C:\IIQSandbox"
)
$ErrorActionPreference = "Stop"

$jdk17 = Get-ChildItem "C:\Program Files\Eclipse Adoptium", $Root -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue | Select-Object -First 1
$env:JAVA_HOME = $jdk17.FullName
$env:Path = "$env:JAVA_HOME\bin;$env:Path"

# 1. CSV feeds -> C:\IIQSandbox\data (path the apps read from)
New-Item -ItemType Directory -Force "$Root\data" | Out-Null
Copy-Item "$Repo\sandbox-data\*.csv" "$Root\data" -Force
Write-Host "Copied feeds to $Root\data"

# 2. Import objects (order matters: rule + correlation before apps, apps before tasks)
$bin = "$Root\tomcat\webapps\identityiq\WEB-INF\bin"
$files = @(
  "Rule\Rule-Sandbox-HR-Creation.xml",
  "CorrelationConfig\Sandbox-Correlation.xml",
  "Application\Sandbox-HR.xml",
  "Application\Sandbox-AD.xml",
  "Application\Sandbox-EBS.xml",
  "TaskDefinition\Sandbox-Agg-HR.xml",
  "TaskDefinition\Sandbox-Agg-AD.xml",
  "TaskDefinition\Sandbox-Agg-EBS.xml",
  "TaskDefinition\Sandbox-Refresh.xml"
)
Push-Location $bin
foreach ($f in $files) {
  Write-Host "Importing $f"
  $path = "$Repo\config\$f" -replace '\\','/'
  $out = cmd /c "iiq.bat console -c `"import $path`"" 2>&1
  $out | Write-Host
  if ($out -match 'Exception|Error|failed') { Pop-Location; throw "Import failed: $f" }
}

# 3. Aggregate HR first (creates identities), then AD + EBS (correlate), then refresh
foreach ($t in "Sandbox-Agg-HR","Sandbox-Agg-AD","Sandbox-Agg-EBS","Sandbox-Refresh") {
  Write-Host "Running $t"
  cmd /c "iiq.bat console -c `"run $t`""
}
Pop-Location

Write-Host "`nDone. Check Setup > Tasks > Task Results, then Identities > Identity Warehouse." -ForegroundColor Green