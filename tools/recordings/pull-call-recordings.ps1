<#
삼성 전화 앱의 자동 통화 녹음을 USB 디버깅(adb)으로 노트북에 가져온다(2026-11-11). 입력 없음. 다시 돌려도 된다.
  - 폰: /sdcard/Recordings/Call/통화 <상대>_<YYMMDD>_<HHMMSS>.m4a  (<상대>는 연락처 이름, 없으면 번호)
    삼성 통화 녹음은 모노(48kHz AAC)라 내 목소리와 상대가 한 채널에 섞여 있다. 수신/발신 구분은 파일 이름에 없다
  - 노트북: <Dest>/call/<YYYY-MM-DD>/<원래 이름>.m4a. 이미 있고 크기가 같으면 건너뜀
  - 가져온 뒤 폰과 노트북의 sha256을 견주고, 같을 때만 제자리 이름으로 바꾼다. 폰 사본은 지우지 않는다
  - <Dest>/call/index.tsv에 한 줄씩(파일, 바이트, sha256, 가져온 시각, 상대, 번호, 통화 시작)
  - 녹음 화면에서 지운 파일(<Dest>/call/deleted.tsv, 묘비)은 건너뜀(result reason deleted)
  - 사본 위치는 PUT /api/recordings/files (device phone, file "call/<날짜>/<이름>")
-ProgressJson·종료 코드는 pull-recordings.ps1과 같다(0 성공, 1 실패한 파일 있음, 2 연결 안 됨, 3 이미 가져오는 중).
#>
param([string] $Dest = (Join-Path $env:USERPROFILE "SecondBrainRecordings"), [string] $RemoteRoot = "/sdcard/Recordings/Call", [switch] $Backfill, [switch] $ProgressJson)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot "pull-common.ps1")
Init-Pull -ProgressJson:$ProgressJson
$localRoot = Join-Path $Dest "call"
$index = Join-Path $localRoot "index.tsv"

# "통화 <상대>_<YYMMDD>_<HHMMSS>.m4a" → 상대(연락처 이름), 번호(이름이 숫자뿐일 때), 시작 시각
function ParseName($name) {
    if ($name -notmatch '^(?:통화\s+)?(.+)_(\d{6})_(\d{6})\.m4a$') { return $null }
    $who = $Matches[1]; $d = $Matches[2]; $t = $Matches[3]
    $num = if ($who -match '^[+\d\-]+$') { $who } else { '' }
    [pscustomobject]@{ Who = $who; Number = $num; Date = "20$($d.Substring(0,2))-$($d.Substring(2,2))-$($d.Substring(4,2))"; Started = "20$($d.Substring(0,2))-$($d.Substring(2,2))-$($d.Substring(4,2))T$($t.Substring(0,2)):$($t.Substring(2,2)):$($t.Substring(4,2))" }
}
function Q($s) { "'" + ($s -replace "'", "'\''") + "'" }   # 셸 작은따옴표

Enter-PullLock
Connect-Phone
New-Item -ItemType Directory -Force $localRoot | Out-Null
if (-not (Test-Path $index)) { "file`tbytes`tsha256`tpulled_at`twho`tnumber`tstarted" | Out-File -Encoding utf8 $index }

$list = & $adb shell "cd $RemoteRoot 2>/dev/null && find . -maxdepth 1 -type f -name '*.m4a' -exec stat -c '%s %n' {} +" 2>$null
$files = @($list | Where-Object { $_ -match '^\d+ \./' } | ForEach-Object { $p = $_.Split(' ', 2); [pscustomobject]@{ Bytes = [long]$p[0]; Name = $p[1].Substring(2) } } | Sort-Object Name)
Write-Host "폰 통화 녹음 $($files.Count)개"
Progress 'plan' @{ kind = 'call'; total = $files.Count; device = $env:ANDROID_SERIAL }

$deleted = Get-Deleted $localRoot
$pulled = 0; $skipped = 0; $bad = 0; $i = 0
foreach ($f in $files) {
    $i++
    if ($deleted.Contains($f.Name)) { $skipped++; Progress 'result' @{ i = $i; status = 'skipped'; name = $f.Name; reason = 'deleted' }; continue }
    $info = ParseName $f.Name
    if (-not $info) { Write-Host "  이름 형식 모름(건너뜀): $($f.Name)" -ForegroundColor Yellow; $skipped++; Progress 'result' @{ i = $i; status = 'skipped'; name = $f.Name; reason = 'name' }; continue }
    $rel = "call/$($info.Date)/$($f.Name)"
    $local = Join-Path $localRoot (Join-Path $info.Date $f.Name)
    if ((Test-Path -LiteralPath $local) -and (Get-Item -LiteralPath $local).Length -eq $f.Bytes) { $skipped++; Progress 'result' @{ i = $i; status = 'skipped'; name = $f.Name }; continue }
    Progress 'file' @{ i = $i; total = $files.Count; name = $f.Name; bytes = $f.Bytes }
    New-Item -ItemType Directory -Force (Split-Path $local) | Out-Null
    $tmp = "$local.pulling"
    & $adb pull "$RemoteRoot/$($f.Name)" $tmp 2>&1 | Out-Null
    if (-not (Test-Path -LiteralPath $tmp)) { Write-Host "  가져오기 실패: $($f.Name)" -ForegroundColor Yellow; $bad++; Progress 'result' @{ i = $i; status = 'failed'; name = $f.Name; reason = 'pull' }; continue }
    $localHash = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()
    $remoteHash = ((& $adb shell "sha256sum $(Q "$RemoteRoot/$($f.Name)")") -split '\s+')[0]
    if ($localHash -ne $remoteHash) { Write-Host "  sha256 다름(폰 $remoteHash, 노트북 $localHash): $($f.Name)" -ForegroundColor Yellow; Remove-Item -LiteralPath $tmp -Force; $bad++; Progress 'result' @{ i = $i; status = 'failed'; name = $f.Name; reason = 'sha256' }; continue }
    Move-Item -LiteralPath $tmp $local -Force
    "$rel`t$($f.Bytes)`t$localHash`t$((Get-Date).ToString('s'))`t$($info.Who)`t$($info.Number)`t$($info.Started)" | Out-File -Append -Encoding utf8 $index
    PutCopy $rel $local $f.Bytes $localHash | Out-Null
    $pulled++
    Progress 'result' @{ i = $i; status = 'pulled'; name = $f.Name; bytes = $f.Bytes }
}
if ($Backfill) {
    $n = 0; Import-Csv $index -Delimiter "`t" | ForEach-Object { if (PutCopy $_.file (Join-Path $Dest ($_.file -replace '/', '\')) ([long]$_.bytes) $_.sha256) { $n++ } }
    Write-Host "위치 다시 적음 $n"
}
Write-Host "가져옴 $pulled · 이미 있음 $skipped · 실패 $bad  → $localRoot"
Progress 'end' @{ kind = 'call'; pulled = $pulled; skipped = $skipped; failed = $bad }
if ($bad) { exit 1 }
