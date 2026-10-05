<#
  IIQ 8.4 local sandbox: Temurin 17 + Tomcat 9 + MySQL 8.0 + SSB deploy
  Run from an elevated PowerShell:
    powershell -ExecutionPolicy Bypass -File .\setup-iiq-sandbox.ps1 -Repo <path to your SSB project>
  The SSB project must contain base\ga\identityiq-8.4.zip (from SailPoint Compass).
  Re-runnable: skips anything already present.
#>
param(
  [Parameter(Mandatory)][string]$Repo,
  [string]$Root = "C:\IIQSandbox"
)

$ErrorActionPreference = "Stop"
$ProgressPreference    = "SilentlyContinue"   # makes Invoke-WebRequest ~10x faster

$TomcatVer = "9.0.98"
$MySqlVer  = "8.0.40"
$JdbcVer   = "8.4.0"

$Tomcat = "$Root\tomcat"
$MySql  = "$Root\mysql"
$Dl     = "$Root\downloads"
New-Item -ItemType Directory -Force $Root, $Dl | Out-Null

function Step($m) { Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Get-File($url, $out) { if (-not (Test-Path $out)) { Write-Host "Downloading $url"; Invoke-WebRequest $url -OutFile $out } }

# ---------------------------------------------------------------- 1. Java 17
Step "Java 17"
$jdk17 = Get-ChildItem "C:\Program Files\Eclipse Adoptium", $Root -Directory -Filter "jdk-17*" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $jdk17) {
  $zip = "$Dl\jdk17.zip"
  Get-File "https://api.adoptium.net/v3/binary/latest/17/ga/windows/x64/jdk/hotspot/normal/eclipse" $zip
  Expand-Archive $zip $Root -Force
  $jdk17 = Get-ChildItem $Root -Directory -Filter "jdk-17*" | Select-Object -First 1
}
$env:JAVA_HOME = $jdk17.FullName
$env:Path = "$env:JAVA_HOME\bin;C:\Tools\apache-ant-1.10.15\bin;$env:Path"
Write-Host "JAVA_HOME (this session) = $env:JAVA_HOME"

# ---------------------------------------------------------------- 2. Tomcat 9
Step "Tomcat $TomcatVer"
if (-not (Test-Path "$Tomcat\bin\startup.bat")) {
  $zip = "$Dl\tomcat.zip"
  Get-File "https://archive.apache.org/dist/tomcat/tomcat-9/v$TomcatVer/bin/apache-tomcat-$TomcatVer-windows-x64.zip" $zip
  Expand-Archive $zip $Root -Force
  Rename-Item "$Root\apache-tomcat-$TomcatVer" $Tomcat
}
@"
set "JAVA_HOME=$($jdk17.FullName)"
set "CATALINA_OPTS=-Xms1g -Xmx4g"
"@ | Set-Content "$Tomcat\bin\setenv.bat" -Encoding ASCII

# MySQL JDBC driver (IIQ does not ship it)
Get-File "https://repo1.maven.org/maven2/com/mysql/mysql-connector-j/$JdbcVer/mysql-connector-j-$JdbcVer.jar" "$Tomcat\lib\mysql-connector-j-$JdbcVer.jar"

# ---------------------------------------------------------------- 3. MySQL 8.0
Step "MySQL $MySqlVer"
if (-not (Test-Path "$MySql\bin\mysqld.exe")) {
  $zip = "$Dl\mysql.zip"
  Get-File "https://cdn.mysql.com/archives/mysql-8.0/mysql-$MySqlVer-winx64.zip" $zip
  Expand-Archive $zip $Root -Force
  Rename-Item "$Root\mysql-$MySqlVer-winx64" $MySql
}
$myIni = "$MySql\my.ini"
@"
[mysqld]
basedir=$($MySql -replace '\\','/')
datadir=$($MySql -replace '\\','/')/data
port=3306
max_allowed_packet=256M
character-set-server=utf8mb4
log_bin_trust_function_creators=1
"@ | Set-Content $myIni -Encoding ASCII

if (-not (Test-Path "$MySql\data")) {
  & "$MySql\bin\mysqld.exe" --defaults-file="$myIni" --initialize-insecure --console
}
if (-not (Get-Process mysqld -ErrorAction SilentlyContinue)) {
  Start-Process "$MySql\bin\mysqld.exe" -ArgumentList "--defaults-file=`"$myIni`"" -WindowStyle Hidden
}
for ($i = 0; $i -lt 30; $i++) {
  & "$MySql\bin\mysqladmin.exe" -u root ping 2>$null | Out-Null
  if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2
}
if ($LASTEXITCODE -ne 0) { throw "MySQL did not start. Check $MySql\data\*.err" }
Write-Host "MySQL is up (root has no password - local sandbox only)"

# ---------------------------------------------------------------- 4. Point SSB at the sandbox
Step "Configure SSB"
Set-Location $Repo
$iiqHome = ($Tomcat -replace '\\','/') + "/webapps/identityiq"
$bp = Get-Content build.properties
if ($bp -match '^IIQHome=') { $bp = $bp -replace '^IIQHome=.*', "IIQHome=$iiqHome" } else { $bp += "IIQHome=$iiqHome" }
$bp | Set-Content build.properties

# Use the stock iiq.properties from the GA war - already defaults to local MySQL
# (identityiq / identityiqPlugin / identityiqah with matching encrypted default passwords)
Add-Type -AssemblyName System.IO.Compression.FileSystem
$gaZip = Get-ChildItem "$Repo\base\ga" -Filter "identityiq-*.zip" | Select-Object -First 1
$ga  = [IO.Compression.ZipFile]::OpenRead($gaZip.FullName)
$war = $ga.Entries | Where-Object Name -eq 'identityiq.war' | Select-Object -First 1
$w   = New-Object IO.Compression.ZipArchive($war.Open())
$p   = $w.Entries | Where-Object FullName -eq 'WEB-INF/classes/iiq.properties'
$sr  = New-Object IO.StreamReader($p.Open()); $stock = $sr.ReadToEnd(); $sr.Dispose()
$w.Dispose(); $ga.Dispose()
[IO.File]::WriteAllText("$Repo\sandbox.iiq.properties", $stock)
Write-Host "sandbox.iiq.properties = stock 8.4 iiq.properties (MySQL defaults)"

# ---------------------------------------------------------------- 5. Build + create DB schema
Step "Build WAR"
$env:SPTARGET = "sandbox"
cmd /c "build.bat clean war"
if ($LASTEXITCODE -ne 0) { throw "Build failed" }

Step "Create IIQ database"
$exists = & "$MySql\bin\mysql.exe" -u root -N -e "SHOW DATABASES LIKE 'identityiq';"
if (-not $exists) {
  $ddl = Get-ChildItem "$Repo\build\extract\WEB-INF\database" -Filter "create_identityiq_tables*.mysql" |
         Where-Object { $_.Name -notmatch 'upgrade' } | Select-Object -First 1
  Write-Host "Running $($ddl.Name)"
  cmd /c "`"$MySql\bin\mysql.exe`" -u root < `"$($ddl.FullName)`""
  if ($LASTEXITCODE -ne 0) { throw "DDL failed" }
} else { Write-Host "identityiq database already exists - skipping DDL" }

# ---------------------------------------------------------------- 6. Deploy + import
Step "Deploy to Tomcat"
& "$Tomcat\bin\shutdown.bat" 2>$null | Out-Null
cmd /c "build.bat deploy"
if ($LASTEXITCODE -ne 0) { throw "Deploy failed" }

Step "Import init objects"
Push-Location "$Tomcat\webapps\identityiq\WEB-INF\bin"
cmd /c "iiq.bat import init.xml"
cmd /c "iiq.bat import init-lcm.xml"
Pop-Location

# ---------------------------------------------------------------- 7. Start
Step "Start Tomcat"
Start-Process "$Tomcat\bin\startup.bat" -WorkingDirectory "$Tomcat\bin"
Write-Host "`nGive it ~60-90s, then open:  http://localhost:8080/identityiq   (spadmin / admin)" -ForegroundColor Green
Write-Host "Stop:   $Tomcat\bin\shutdown.bat   and   $MySql\bin\mysqladmin -u root shutdown"
Write-Host "Start:  start mysqld (see step 3) then $Tomcat\bin\startup.bat"