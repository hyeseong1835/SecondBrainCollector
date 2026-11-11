<#
폰 녹음 파일을 USB 디버깅(adb)으로 노트북에 가져온다(SecondBrain 녹음 1차, 2026-11-11). 입력 없음. 다시 돌려도 된다.
  - 폰: /sdcard/Android/data/dev.hyeseong.secondbrain.collector/files/recordings/<날짜>/<이름>.m4a (쓰는 중인 .part는 건너뜀)
  - 노트북: <Dest>/phone/<날짜>/<이름>.m4a. 이미 있고 크기가 같으면 건너뜀
  - 가져온 뒤 폰과 노트북의 sha256을 견주고, 같을 때만 제자리 이름으로 바꾼다. 폰 사본은 지우지 않는다
  - <Dest>/phone/index.tsv에 한 줄씩(파일, 바이트, sha256, 가져온 시각)
무선 디버깅(adb pair/connect)으로 붙어 있어도 같은 스크립트로 된다. 기기가 여럿이면 ANDROID_SERIAL로 고른다(없으면 USB 쪽).
-ProgressJson: "@@progress {json}" 줄(plan·file·result·end, 연결 안 됨은 error, 이미 도는 중이면 busy)도 낸다(데스크톱 녹음 화면).
종료 코드: 0 성공, 1 실패한 파일 있음, 2 연결 안 됨, 3 이미 가져오는 중.
#>
param([string] $Dest = (Join-Path $env:USERPROFILE "SecondBrainRecordings"), [string] $Package = "dev.hyeseong.secondbrain.collector", [switch] $Backfill, [switch] $ProgressJson)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot "pull-common.ps1")
Init-Pull -ProgressJson:$ProgressJson
$remoteRoot = "/sdcard/Android/data/$Package/files/recordings"
$localRoot = Join-Path $Dest "phone"
$index = Join-Path $localRoot "index.tsv"

Enter-PullLock
Connect-Phone
New-Item -ItemType Directory -Force $localRoot | Out-Null
if (-not (Test-Path $index)) { "file`tbytes`tsha256`tpulled_at" | Out-File -Encoding utf8 $index }

# "크기 경로" 목록(.m4a만)
$list = & $adb shell "cd $remoteRoot 2>/dev/null && find . -type f -name '*.m4a' -exec stat -c '%s %n' {} +" 2>$null
$files = @($list | Where-Object { $_ -match '^\d+ \./' } | ForEach-Object { $p = $_.Split(' ', 2); [pscustomobject]@{ Bytes = [long]$p[0]; Rel = $p[1].Substring(2) } } | Sort-Object Rel)
Write-Host "폰 녹음 파일 $($files.Count)개"
Progress 'plan' @{ kind = 'phone'; total = $files.Count; device = $env:ANDROID_SERIAL }

$pulled = 0; $skipped = 0; $bad = 0; $i = 0
foreach ($f in $files) {
    $i++
    $local = Join-Path $localRoot ($f.Rel -replace '/', '\')
    if ((Test-Path $local) -and (Get-Item $local).Length -eq $f.Bytes) { $skipped++; Progress 'result' @{ i = $i; status = 'skipped'; name = $f.Rel }; continue }   # 위치는 가져올 때 이미 적었다
    Progress 'file' @{ i = $i; total = $files.Count; name = $f.Rel; bytes = $f.Bytes }
    New-Item -ItemType Directory -Force (Split-Path $local) | Out-Null
    $tmp = "$local.pulling"
    & $adb pull "$remoteRoot/$($f.Rel)" $tmp 2>&1 | Out-Null
    if (-not (Test-Path $tmp)) { Write-Host "  가져오기 실패: $($f.Rel)" -ForegroundColor Yellow; $bad++; Progress 'result' @{ i = $i; status = 'failed'; name = $f.Rel; reason = 'pull' }; continue }
    $localHash = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
    $remoteHash = ((& $adb shell "sha256sum '$remoteRoot/$($f.Rel)'") -split '\s+')[0]
    if ($localHash -ne $remoteHash) { Write-Host "  sha256 다름(폰 $remoteHash, 노트북 $localHash): $($f.Rel)" -ForegroundColor Yellow; Remove-Item $tmp -Force; $bad++; Progress 'result' @{ i = $i; status = 'failed'; name = $f.Rel; reason = 'sha256' }; continue }
    Move-Item $tmp $local -Force
    "$($f.Rel)`t$($f.Bytes)`t$localHash`t$((Get-Date).ToString('s'))" | Out-File -Append -Encoding utf8 $index
    PutCopy $f.Rel $local $f.Bytes $localHash | Out-Null
    $pulled++
    Progress 'result' @{ i = $i; status = 'pulled'; name = $f.Rel; bytes = $f.Bytes }
}
if ($Backfill) {   # 이미 가져온 파일의 위치를 index.tsv에서 다시 적는다
    $n = 0; Import-Csv $index -Delimiter "`t" | ForEach-Object { if (PutCopy $_.file (Join-Path $localRoot ($_.file -replace '/', '\')) ([long]$_.bytes) $_.sha256) { $n++ } }
    Write-Host "위치 다시 적음 $n"
}
Write-Host "가져옴 $pulled · 이미 있음 $skipped · 실패 $bad  → $localRoot"
Progress 'end' @{ kind = 'phone'; pulled = $pulled; skipped = $skipped; failed = $bad }
if ($bad) { exit 1 }
