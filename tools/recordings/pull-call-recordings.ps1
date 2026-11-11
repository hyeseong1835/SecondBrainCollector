<#
삼성 전화 앱의 자동 통화 녹음을 USB 디버깅(adb)으로 노트북에 가져온다(2026-11-11). 입력 없음. 다시 돌려도 된다.
  - 폰: /sdcard/Recordings/Call/통화 <상대>_<YYMMDD>_<HHMMSS>.m4a  (<상대>는 연락처 이름, 없으면 번호)
    삼성 통화 녹음은 모노(48kHz AAC)라 내 목소리와 상대가 한 채널에 섞여 있다. 수신/발신 구분은 파일 이름에 없다
  - 노트북: <Dest>/call/<YYYY-MM-DD>/<원래 이름>.m4a. 이미 있고 크기가 같으면 건너뜀
  - 가져온 뒤 폰과 노트북의 sha256을 견주고, 같을 때만 제자리 이름으로 바꾼다. 폰 사본은 지우지 않는다
  - <Dest>/call/index.tsv에 한 줄씩(파일, 바이트, sha256, 가져온 시각, 상대, 번호, 통화 시작)
  - 사본 위치는 PUT /api/recordings/files (device phone, file "call/<날짜>/<이름>")
#>
param([string] $Dest = (Join-Path $env:USERPROFILE "SecondBrainRecordings"), [string] $RemoteRoot = "/sdcard/Recordings/Call", [switch] $Backfill)
$ErrorActionPreference = 'Continue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8   # adb 출력의 한글 이름
$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$localRoot = Join-Path $Dest "call"
$index = Join-Path $localRoot "index.tsv"

$apiCfg = Get-Content (Join-Path $env:LOCALAPPDATA "SecondBrain\api.json") -Raw | ConvertFrom-Json
function PutCopy($rel, $path, $bytes, $sha) {
    $body = @{ device = 'phone'; file = $rel; location = 'laptop'; path = $path; bytes = $bytes; sha256 = $sha } | ConvertTo-Json -Compress
    try { Invoke-RestMethod "$($apiCfg.url.TrimEnd('/'))/api/recordings/files" -Method Put -Headers @{ Authorization = "Bearer $($apiCfg.key)" } -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null; $true }
    catch { Write-Host "  위치 기록 실패: $rel $($_.ErrorDetails.Message)" -ForegroundColor Yellow; $false }
}
# "통화 <상대>_<YYMMDD>_<HHMMSS>.m4a" → 상대(연락처 이름), 번호(이름이 숫자뿐일 때), 시작 시각
function ParseName($name) {
    if ($name -notmatch '^(?:통화\s+)?(.+)_(\d{6})_(\d{6})\.m4a$') { return $null }
    $who = $Matches[1]; $d = $Matches[2]; $t = $Matches[3]
    $num = if ($who -match '^[+\d\-]+$') { $who } else { '' }
    [pscustomobject]@{ Who = $who; Number = $num; Date = "20$($d.Substring(0,2))-$($d.Substring(2,2))-$($d.Substring(4,2))"; Started = "20$($d.Substring(0,2))-$($d.Substring(2,2))-$($d.Substring(4,2))T$($t.Substring(0,2)):$($t.Substring(2,2)):$($t.Substring(4,2))" }
}
function Q($s) { "'" + ($s -replace "'", "'\''") + "'" }   # 셸 작은따옴표

& $adb reconnect offline 2>$null | Out-Null
if (-not ((& $adb devices) -match "`tdevice$")) { Write-Host "폰이 연결되지 않았다(USB 디버깅 허용 확인)" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force $localRoot | Out-Null
if (-not (Test-Path $index)) { "file`tbytes`tsha256`tpulled_at`twho`tnumber`tstarted" | Out-File -Encoding utf8 $index }

$list = & $adb shell "cd $RemoteRoot 2>/dev/null && find . -maxdepth 1 -type f -name '*.m4a' -exec stat -c '%s %n' {} +" 2>$null
$files = @($list | Where-Object { $_ -match '^\d+ \./' } | ForEach-Object { $p = $_.Split(' ', 2); [pscustomobject]@{ Bytes = [long]$p[0]; Name = $p[1].Substring(2) } })
Write-Host "폰 통화 녹음 $($files.Count)개"

$pulled = 0; $skipped = 0; $bad = 0
foreach ($f in $files) {
    $info = ParseName $f.Name
    if (-not $info) { Write-Host "  이름 형식 모름(건너뜀): $($f.Name)" -ForegroundColor Yellow; continue }
    $rel = "call/$($info.Date)/$($f.Name)"
    $local = Join-Path $localRoot (Join-Path $info.Date $f.Name)
    if ((Test-Path -LiteralPath $local) -and (Get-Item -LiteralPath $local).Length -eq $f.Bytes) { $skipped++; continue }
    New-Item -ItemType Directory -Force (Split-Path $local) | Out-Null
    $tmp = "$local.pulling"
    & $adb pull "$RemoteRoot/$($f.Name)" $tmp 2>&1 | Out-Null
    if (-not (Test-Path -LiteralPath $tmp)) { Write-Host "  가져오기 실패: $($f.Name)" -ForegroundColor Yellow; $bad++; continue }
    $localHash = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLower()
    $remoteHash = ((& $adb shell "sha256sum $(Q "$RemoteRoot/$($f.Name)")") -split '\s+')[0]
    if ($localHash -ne $remoteHash) { Write-Host "  sha256 다름(폰 $remoteHash, 노트북 $localHash): $($f.Name)" -ForegroundColor Yellow; Remove-Item -LiteralPath $tmp -Force; $bad++; continue }
    Move-Item -LiteralPath $tmp $local -Force
    "$rel`t$($f.Bytes)`t$localHash`t$((Get-Date).ToString('s'))`t$($info.Who)`t$($info.Number)`t$($info.Started)" | Out-File -Append -Encoding utf8 $index
    PutCopy $rel $local $f.Bytes $localHash | Out-Null
    $pulled++
}
if ($Backfill) {
    $n = 0; Import-Csv $index -Delimiter "`t" | ForEach-Object { if (PutCopy $_.file (Join-Path $Dest ($_.file -replace '/', '\')) ([long]$_.bytes) $_.sha256) { $n++ } }
    Write-Host "위치 다시 적음 $n"
}
Write-Host "가져옴 $pulled · 이미 있음 $skipped · 실패 $bad  → $localRoot"
if ($bad) { exit 1 }
