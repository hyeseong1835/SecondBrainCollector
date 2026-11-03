# LifeLog PC 설정: Windows 수집기·대시보드를 Supabase에 붙인다(로컬 Docker DB에서 옮길 때도).
# 관리자 접속으로 이 기기의 비밀번호를 재발급하고(처음이면 등록), 수집기 config.json에 DPAPI로 넣는다. 대시보드는 수집기 설정을 그대로 쓴다.
# 비밀번호는 프롬프트에서 받고 어디에도 저장하지 않는다. 사용자가 일반 PowerShell에서 직접 실행한다(Claude 도구 셸에서 돌리지 않는다).
#
#   powershell -ExecutionPolicy Bypass -File tools/db/pc-setup.ps1 -ProjectRef <프로젝트ref>
#   옵션: -Device laptop  -Enable claude-session,vscode,...(켤 수집기 플러그인)  -SkipMigrate
param(
    [Parameter(Mandatory = $true)][string]$ProjectRef,
    [string]$DbHost = "aws-0-ap-northeast-1.pooler.supabase.com",
    [int]$Port = 5432,
    [string]$Database = "postgres",
    [string]$Device = "laptop",
    [string[]]$Enable = @("claude-session", "vscode", "win-afk", "win-input", "win-media", "win-power", "win-window"),
    [switch]$SkipMigrate
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root
$bin = "bin\Debug\net10.0-windows10.0.19041.0"
$collector = Join-Path $root "collector-windows\src\LifeLog.Collector.Windows\$bin\lifelog-collector.exe"
$dashboard = Join-Path $root "dashboard-windows\src\LifeLog.Dashboard.Windows\$bin\lifelog-dashboard.exe"
if (-not (Test-Path $collector)) { throw "수집기 빌드가 없습니다: dotnet build collector-windows" }

$secure = Read-Host "Supabase DB 비밀번호 (postgres)" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure))
# 공백·따옴표가 든 비밀번호도 되게 작은따옴표로 감싼다(libpq conninfo 규칙). Npgsql 접속 문자열은 따로 만든다
$esc = $plain.Replace('\', '\\').Replace("'", "\'")
$libpq = "host=$DbHost port=$Port dbname=$Database user=postgres.$ProjectRef password='$esc' sslmode=require"
$npgsql = "Host=$DbHost;Port=$Port;Database=$Database;Username=postgres.$ProjectRef;Password=`"$($plain.Replace('"', '""'))`";SSL Mode=Require"
$plain = $null; $esc = $null

try {
    if (-not $SkipMigrate) {
        Write-Host "== 1. 코어 스키마 마이그레이션"
        $env:LIFELOG_ADMIN_DSN = $libpq
        python tools/db/migrate.py
        if ($LASTEXITCODE -ne 0) { throw "마이그레이션 실패" }
    }

    Write-Host "== 2. 실행 중인 수집기·대시보드 끄기"
    & $collector stop | Out-Null
    Get-Process lifelog-collector -ErrorAction SilentlyContinue | ForEach-Object { $_.WaitForExit(30000) | Out-Null }
    Get-Process lifelog-dashboard -ErrorAction SilentlyContinue | ForEach-Object { $_.CloseMainWindow() | Out-Null; $_.WaitForExit(15000) | Out-Null }

    Write-Host "== 3. 기기 '$Device' 비밀번호 재발급(없으면 등록)"
    $env:LIFELOG_ADMIN_DSN = $npgsql
    $common = @("--device", $Device, "--host", $DbHost, "--port", "$Port", "--database", $Database, "--tenant", $ProjectRef)
    & $collector setup-reset @common
    if ($LASTEXITCODE -ne 0) {
        Write-Host "   재발급 실패, 새로 등록합니다"
        & $collector setup @common
        if ($LASTEXITCODE -ne 0) { throw "기기 설정 실패" }
    }
} finally {
    $env:LIFELOG_ADMIN_DSN = $null
    $libpq = $null; $npgsql = $null
}

Write-Host "== 4. 수집기 시작(플러그인 등록) 후 켤 플러그인 켜기"
Start-Process $collector -ArgumentList "run", "--tray"
Start-Sleep -Seconds 15
foreach ($id in $Enable) {
    & $collector enable $id | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host "   켬: $id" } else { Write-Host "   켜기 실패: $id" }
}
& $collector reload | Out-Null

Write-Host "== 5. 대시보드 시작"
if (Test-Path $dashboard) { Start-Process $dashboard }
Write-Host "== 완료. 로그: $env:LOCALAPPDATA\LifeLog\collector\collector.log"
