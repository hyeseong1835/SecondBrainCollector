<#
pull-recordings.ps1 · pull-call-recordings.ps1가 같이 쓰는 것(2026-11-11). 점 소스로 읽는다: . "$PSScriptRoot\pull-common.ps1"
  - $adb: %LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe
  - PutCopy: 사본 위치를 SecondBrain API(PUT /api/recordings/files)에 적는다. 키는 훅 키(%LOCALAPPDATA%\SecondBrain\api.json)
  - Progress: -ProgressJson이면 "@@progress {json}" 한 줄(데스크톱 녹음 화면이 읽음. analyze.py --progress-json과 같은 방식)
  - Enter-PullLock: 가져오기 동시 실행 막기(두 스크립트가 같은 이름 뮤텍스). 이미 돌면 busy를 내고 종료 코드 3
  - Get-Deleted: 녹음 화면에서 지운 파일(<출처 폴더>\deleted.tsv, 묘비)의 이름 모음. 가져오기는 이 이름을 건너뛴다(지운 파일을 다시 가져오지 않게)
  - Connect-Phone: adb 연결 확인. 안 되면 이유(no-device·unauthorized·offline)를 내고 종료 코드 2.
    기기가 여럿이면(USB와 무선이 같이 붙는 등) USB 쪽을 ANDROID_SERIAL로 골라 뒤의 adb 명령이 모두 그 기기로 간다
#>
$adb = Join-Path $env:LOCALAPPDATA "Android\Sdk\platform-tools\adb.exe"
$script:ProgressOn = $false

function Progress([string] $name, [hashtable] $kw = @{}) {
    if (-not $script:ProgressOn) { return }
    $o = [ordered]@{ event = $name }
    foreach ($k in $kw.Keys) { $o[$k] = $kw[$k] }
    [Console]::Out.WriteLine("@@progress " + ($o | ConvertTo-Json -Compress))
    [Console]::Out.Flush()
}

function Init-Pull([switch] $ProgressJson) {
    $script:ProgressOn = [bool]$ProgressJson
    [Console]::OutputEncoding = [Text.Encoding]::UTF8   # adb 출력의 한글 이름, 앱이 UTF-8로 읽음
}

$script:PullMutex = $null
function Enter-PullLock {
    $m = New-Object Threading.Mutex($false, 'Local\SecondBrainRecordingsPull')
    $got = $false
    try { $got = $m.WaitOne(0) } catch [Threading.AbandonedMutexException] { $got = $true }   # 앞 실행이 죽으며 놓은 것
    if (-not $got) {
        Write-Host "이미 휴대폰 녹음 가져오기가 돌고 있다" -ForegroundColor Red
        Progress 'busy'
        exit 3
    }
    $script:PullMutex = $m   # 프로세스가 끝나면 풀린다
}

$apiCfg = Get-Content (Join-Path $env:LOCALAPPDATA "SecondBrain\api.json") -Raw | ConvertFrom-Json
function PutCopy($rel, $path, $bytes, $sha) {
    $body = @{ device = 'phone'; file = $rel; location = 'laptop'; path = $path; bytes = $bytes; sha256 = $sha } | ConvertTo-Json -Compress
    try { Invoke-RestMethod "$($apiCfg.url.TrimEnd('/'))/api/recordings/files" -Method Put -Headers @{ Authorization = "Bearer $($apiCfg.key)" } -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null; $true }
    catch { Write-Host "  위치 기록 실패: $rel $($_.ErrorDetails.Message)" -ForegroundColor Yellow; $false }
}

# 묘비(2026-11-11): 데스크톱 녹음 화면이 파일을 영구 삭제할 때 <출처 폴더>\deleted.tsv에 한 줄씩(name·file·deleted_at·phone_deleted) 적는다.
# 폰 원본을 지우지 않았으면 다음 가져오기 때 다시 받게 되므로, 이름(폴더 없이)이 여기 있으면 건너뛴다
function Get-Deleted($localRoot) {
    $set = New-Object 'Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $p = Join-Path $localRoot "deleted.tsv"
    if (Test-Path -LiteralPath $p) { Import-Csv -LiteralPath $p -Delimiter "`t" -Encoding UTF8 | ForEach-Object { if ($_.name) { [void]$set.Add($_.name) } } }
    , $set
}

function Connect-Phone {
    if (-not (Test-Path $adb)) {
        Write-Host "adb가 없다: $adb" -ForegroundColor Red
        Progress 'error' @{ reason = 'no-adb'; message = "adb가 없습니다: $adb" }
        exit 2
    }
    & $adb reconnect offline 2>$null | Out-Null
    $rows = @(& $adb devices 2>$null | Select-Object -Skip 1 | Where-Object { $_ -match "^(\S+)\t(\S+)" } | ForEach-Object { [pscustomobject]@{ Serial = $Matches[1]; State = $Matches[2] } })
    if ($env:ANDROID_SERIAL) { $rows = @($rows | Where-Object Serial -eq $env:ANDROID_SERIAL) }
    $ok = @($rows | Where-Object State -eq 'device')
    if ($ok.Count -gt 0) {
        $usb = @($ok | Where-Object { $_.Serial -notmatch ':|_adb-tls-connect' })
        $pick = if ($usb.Count) { $usb[0] } else { $ok[0] }
        $env:ANDROID_SERIAL = $pick.Serial
        return
    }
    $reason, $msg = if ($rows | Where-Object State -eq 'unauthorized') { 'unauthorized', "폰에서 'USB 디버깅을 허용할까요?' 창을 허용해 주세요" }
                    elseif ($rows | Where-Object State -eq 'offline') { 'offline', "폰 연결이 끊겨 있습니다(케이블을 다시 꽂거나 무선 디버깅을 다시 연결)" }
                    else { 'no-device', "연결된 폰이 없습니다(USB 디버깅 허용 확인, 무선은 adb mdns services로 찾아 adb connect)" }
    Write-Host "폰이 연결되지 않았다: $msg" -ForegroundColor Red
    Progress 'error' @{ reason = $reason; message = $msg }
    exit 2
}
