# Starts the mock Copperleaf SCIM 2.0 server. Add -Reset to reseed from C:\IIQSandbox\data\hr_employees.csv
param([switch]$Reset)
$args2 = @("scim_server.py")
if ($Reset) { $args2 += "--reset" }
Set-Location $PSScriptRoot
python @args2
