<#
폰 녹음 파일을 USB 디버깅(adb)으로 노트북에 가져온다(SecondBrain 녹음 1차, 2026-11-11). 입력 없음. 다시 돌려도 된다.
  - 폰: /sdcard/Android/data/dev.hyeseong.secondbrain.collector/files/recordings/<날짜>/<이름>.m4a (쓰는 중인 .part는 건너뜀)
  - 노트북: <Dest>/phone/<날짜>/<이름>.m4a. 이미 있고 크기가 같으면 건너뜀
  - 가져온 뒤 폰과 노트북의 sha256을 견주고, 같을 때만 제자리 이름으로 바꾼다. 폰 사본은 지우지 않는다
  - <Dest>/phone/index.tsv에 한 줄씩(파일, 바이트, sha256, 가져온 시각)
무선 디버깅(adb pair/connect)으로 붙어 있어도 같은 스크립트로 된다.
#>
param([string] $Dest = (Join-Path $env:USERPROFILE "SecondBrainRecordings"), [string] $Package = "dev.hyeseong.secondbrain.collector")
$ErrorActionPreference = 'Continue'
$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$remoteRoot = "/sdcard/Android/data/$Package/files/recordings"
$localRoot = Join-Path $Dest "phone"
$index = Join-Path $localRoot "index.tsv"

& $adb reconnect offline 2>$null | Out-Null
if (-not ((& $adb devices) -match "`tdevice$")) { Write-Host "폰이 연결되지 않았다(USB 디버깅 허용 확인)" -ForegroundColor Red; exit 1 }
New-Item -ItemType Directory -Force $localRoot | Out-Null
if (-not (Test-Path $index)) { "file`tbytes`tsha256`tpulled_at" | Out-File -Encoding utf8 $index }

# "크기 경로" 목록(.m4a만)
$list = & $adb shell "cd $remoteRoot 2>/dev/null && find . -type f -name '*.m4a' -exec stat -c '%s %n' {} +" 2>$null
$files = @($list | Where-Object { $_ -match '^\d+ \./' } | ForEach-Object { $p = $_.Split(' ', 2); [pscustomobject]@{ Bytes = [long]$p[0]; Rel = $p[1].Substring(2) } })
Write-Host "폰 녹음 파일 $($files.Count)개"

$pulled = 0; $skipped = 0; $bad = 0
foreach ($f in $files) {
    $local = Join-Path $localRoot ($f.Rel -replace '/', '\')
    if ((Test-Path $local) -and (Get-Item $local).Length -eq $f.Bytes) { $skipped++; continue }
    New-Item -ItemType Directory -Force (Split-Path $local) | Out-Null
    $tmp = "$local.pulling"
    & $adb pull "$remoteRoot/$($f.Rel)" $tmp 2>&1 | Out-Null
    if (-not (Test-Path $tmp)) { Write-Host "  가져오기 실패: $($f.Rel)" -ForegroundColor Yellow; $bad++; continue }
    $localHash = (Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower()
    $remoteHash = ((& $adb shell "sha256sum '$remoteRoot/$($f.Rel)'") -split '\s+')[0]
    if ($localHash -ne $remoteHash) { Write-Host "  sha256 다름(폰 $remoteHash, 노트북 $localHash): $($f.Rel)" -ForegroundColor Yellow; Remove-Item $tmp -Force; $bad++; continue }
    Move-Item $tmp $local -Force
    "$($f.Rel)`t$($f.Bytes)`t$localHash`t$((Get-Date).ToString('s'))" | Out-File -Append -Encoding utf8 $index
    $pulled++
}
Write-Host "가져옴 $pulled · 이미 있음 $skipped · 실패 $bad  → $localRoot"
if ($bad) { exit 1 }
