# 수집기 플러그인 하나를 DB에서 지운다(관리자 접속). 비밀번호는 프롬프트에서 받고 어디에도 저장하지 않는다.
# 사용자가 일반 PowerShell에서 직접 실행한다(Claude 도구 셸에서 돌리지 않는다).
#
#   powershell -ExecutionPolicy Bypass -File tools/db/drop-plugin.ps1 -ProjectRef <프로젝트ref> -Plugin phone-wifi [-DryRun]
param(
    [Parameter(Mandatory = $true)][string]$ProjectRef,
    [Parameter(Mandatory = $true)][string]$Plugin,
    [string]$DbHost = "aws-0-ap-northeast-1.pooler.supabase.com",
    [int]$Port = 5432,
    [string]$Database = "postgres",
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $root

$secure = Read-Host "Supabase DB 비밀번호 (postgres)" -AsSecureString
$plain = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure))
$esc = $plain.Replace('\', '\').Replace("'", "\'")
$env:LIFELOG_ADMIN_DSN = "host=$DbHost port=$Port dbname=$Database user=postgres.$ProjectRef password='$esc' sslmode=require"
$plain = $null
$esc = $null

$pyArgs = @("tools/db/drop-plugin.py", $Plugin)
if ($DryRun) { $pyArgs += "--dry-run" }
if ($Force) { $pyArgs += "--force" }
try { python @pyArgs } finally { $env:LIFELOG_ADMIN_DSN = $null }
