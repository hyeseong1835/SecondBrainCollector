<#
확인 스크립트: Windows 수집기(secondbrain-collector)를 끄고 → 빌드 → 바로가기로 다시 켜고 → 준비될 때까지 기다린 뒤 한 화면으로 요약한다.
([[작업 흐름 개선안]] 1번, 2026-11-14)

사용법 (이 저장소 어디서든, 주 체크아웃이든 워크트리든)
  powershell -ExecutionPolicy Bypass -File scripts\verify.ps1              # 끄기 → 빌드 → 실행 → 준비 확인
  powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 -NoLaunch    # 빌드만(실행 중인 수집기는 건드리지 않음. 주 체크아웃은 bin이 잠겨 있으면 실패할 수 있음)
  powershell -ExecutionPolicy Bypass -File scripts\verify.ps1 -Restore     # 빌드 없이 지금 수집기를 끄고 시작 메뉴 바로가기(주 체크아웃 exe)로 다시 켠다
  옵션: -SkipTests(시험 건너뛰기. 지금은 시험 프로젝트가 없어 차이 없음) -TimeoutSec 60(준비 대기) -Target windows(대상. 지금은 windows 하나)
종료 코드: 0 통과, 1 실패. ship.ps1 같은 스크립트는 종료 코드만 보면 된다.

동작
- 주 체크아웃: `secondbrain-collector stop`(정상 종료, 안 되면 강제 종료) → `dotnet build collector-windows`(Debug, 시작 메뉴 바로가기가 가리키는 그 exe)
  → 시작 메뉴 `SecondBrain\SecondBrain 수집기.lnk`로 실행.
- 워크트리: 먼저 워크트리를 빌드(실행 중인 수집기 그대로) → 성공하면 수집기를 끄고, 워크트리 exe를 가리키는 임시 바로가기(bin\verify-launch.lnk, run --tray)로 실행.
  시작 메뉴 바로가기·자동 실행(HKCU Run)은 바꾸지 않는다. 원래 수집기로 돌아가려면 `-Restore` 또는 시작 메뉴 바로가기.
- 빌드가 실패하거나 중간에 멈춰도, 이 스크립트가 끈 수집기는 반드시 시작 메뉴 바로가기로 다시 켠다.
- 실행은 explorer.exe로 바로가기를 연다. 그렇게 뜬 프로세스가 곧 끝나면(설정을 못 찾는 경우) 같은 exe를 이 셸에서 직접 `run --tray`로 띄우고 '주의'로 남긴다.
  2026-11-14 확인: 수집기 설정(%LOCALAPPDATA%\SecondBrain\collector)이 Claude 앱 셸의 AppData 사본(Packages\Claude_…\LocalCache)에만 있어 셸 밖에서 뜬 프로세스는 "설정 없음"으로 끝났다.
  그래서 자료 폴더를 %USERPROFILE%\.secondbrain\collector로 옮겼다(처음 실행 때 사본에서 베껴 옴). 이 대비책은 옛 빌드용으로 남겨 둔다.
- 준비 확인: 새 프로세스(경로 확인)가 떠서 살아 있고, collector.log에 `수집기 시작(pid <새 PID>` 줄이 생기면 통과.
  로그에 그 줄이 안 보이면 '주의'. 프로세스가 없거나 곧 끝나면 실패.
- 인증 정보(config.json의 비밀번호 등)는 읽지도 출력하지도 않는다.
#>
param(
    [switch]$SkipTests,
    [switch]$NoLaunch,
    [switch]$Restore,
    [ValidateSet('windows')][string]$Target = 'windows',
    [int]$TimeoutSec = 60
)

$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$isWorktree = Test-Path (Join-Path $root '.git') -PathType Leaf
$mainRoot = $root
if ($isWorktree) {
    $gitdir = ((Get-Content (Join-Path $root '.git') -Raw) -replace '^gitdir:\s*', '').Trim()
    $mainRoot = [IO.Path]::GetFullPath((Join-Path $gitdir '..\..\..'))
}
$exeRel = 'collector-windows\src\LifeLog.Collector.Windows\bin\Debug\net10.0-windows10.0.19041.0\secondbrain-collector.exe'
$exe = Join-Path $root $exeRel
$menuLnk = Join-Path ([Environment]::GetFolderPath('Programs')) 'SecondBrain\SecondBrain 수집기.lnk'
# 자료 폴더는 2026-11-14부터 %USERPROFILE%\.secondbrain(Claude 앱 셸의 AppData 사본 문제로 옮김). SECONDBRAIN_DATA_DIR이 있으면 그곳
$dataDir = if ($env:SECONDBRAIN_DATA_DIR) { $env:SECONDBRAIN_DATA_DIR } else { Join-Path $env:USERPROFILE '.secondbrain' }
$logPath = Join-Path $dataDir 'collector\collector.log'
$procName = 'secondbrain-collector'
$logDir = Join-Path $env:TEMP 'secondbrain-verify'
New-Item -ItemType Directory -Force $logDir | Out-Null
$buildLog = Join-Path $logDir ("collector-{0:yyyyMMdd-HHmmss}.log" -f (Get-Date))

$steps = New-Object System.Collections.Generic.List[object]
function Add-Step([string]$name, [string]$state, [string]$detail) { $steps.Add([pscustomobject]@{ Name = $name; State = $state; Detail = $detail }) }
$tail = @()
$stoppedByUs = $false
$launched = $false

function Stop-Collector {
    $procs = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
    if ($procs.Count -eq 0) { return '실행 중 아님' }
    $ids = ($procs | ForEach-Object Id) -join ','
    # 정상 종료(열린 구간 닫고 큐 비움). 실행 중인 exe로 stop을 보낸다
    $runningExe = $procs[0].Path
    if ($runningExe -and (Test-Path $runningExe)) {
        try {
            $p = Start-Process -FilePath $runningExe -ArgumentList 'stop' -WindowStyle Hidden -PassThru
            if (-not $p.WaitForExit(35000)) { $p.Kill() }
        } catch { }
    }
    $deadline = (Get-Date).AddSeconds(5)
    while ((Get-Process -Name $procName -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 300 }
    $left = @(Get-Process -Name $procName -ErrorAction SilentlyContinue)
    if ($left.Count -gt 0) {
        $left | Stop-Process -Force
        Start-Sleep -Milliseconds 500
        return "PID $ids 강제 종료(정상 종료 신호가 닿지 않음)"
    }
    return "PID $ids 정상 종료"
}

function Open-Shortcut([string]$lnk) {
    # explorer가 이미 떠 있는 셸에 넘겨 샌드박스 밖에서 실행된다. explorer.exe의 종료 코드는 늘 1이라 보지 않는다
    Start-Process -FilePath "$env:WINDIR\explorer.exe" -ArgumentList "`"$lnk`""
}

# 바로가기로 띄우고, 곧 끝나면(셸 밖에서 뜬 프로세스가 설정을 못 찾는 환경, 2026-11-14 확인) 같은 exe를 직접 띄운다
function Start-AndWait([string]$lnk, [string]$exePath, [string]$label) {
    $t = Get-Date
    Open-Shortcut $lnk; $script:launched = $true
    Add-Step '실행' '통과' $label
    $r = Wait-Ready $exePath $t
    if ($r[0] -eq '실패' -and $exePath -and -not (Get-Process -Name $procName -ErrorAction SilentlyContinue)) {
        Add-Step '준비 확인' '주의' "바로가기 실행: $($r[1])"
        $t = Get-Date
        Start-Process -FilePath $exePath -ArgumentList 'run', '--tray' -WorkingDirectory (Split-Path $exePath -Parent) -WindowStyle Hidden
        $r = Wait-Ready $exePath $t
        Add-Step '직접 실행' $r[0] "$exePath run --tray: $($r[1])"
        return
    }
    Add-Step '준비 확인' $r[0] $r[1]
}

function Wait-Ready([string]$expectedExe, [datetime]$since) {
    $deadline = $since.AddSeconds($TimeoutSec)
    $proc = $null
    while ((Get-Date) -lt $deadline) {
        $proc = Get-Process -Name $procName -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -ge $since.AddSeconds(-2) } | Select-Object -First 1
        if ($proc) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $proc) {
        $old = Get-Process -Name $procName -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($old) { return @('실패', "새 프로세스가 뜨지 않음(이전 PID $($old.Id)가 아직 실행 중)") }
        return @('실패', "${TimeoutSec}초 안에 프로세스가 뜨지 않음")
    }
    $detail = "PID $($proc.Id)"
    if ($expectedExe -and $proc.Path -and ($proc.Path -ne $expectedExe)) { $detail += ", 경로가 다름: $($proc.Path)" }
    # 로그에 이 PID의 시작 줄이 생길 때까지
    $logOk = $false
    while ((Get-Date) -lt $deadline) {
        if ($proc.HasExited) { return @('실패', "PID $($proc.Id)가 곧 끝남(종료 코드 $($proc.ExitCode))") }
        if ((Test-Path $logPath) -and (Select-String -Path $logPath -Pattern "수집기 시작\(pid $($proc.Id)\b" -SimpleMatch:$false -Quiet)) { $logOk = $true; break }
        Start-Sleep -Milliseconds 700
    }
    Start-Sleep -Seconds 3   # 시작 직후 실패(설정 없음·뮤텍스)로 끝나는지 조금 더 본다
    $proc.Refresh()
    if ($proc.HasExited) { return @('실패', "PID $($proc.Id)가 곧 끝남(종료 코드 $($proc.ExitCode))") }
    $sec = [math]::Round(((Get-Date) - $since).TotalSeconds, 1)
    if ($expectedExe -and $proc.Path -and ($proc.Path -ne $expectedExe)) { return @('주의', "$detail, ${sec}초") }
    if (-not $logOk) { return @('주의', "$detail 실행 중, 로그에 시작 줄이 안 보임(샌드박스 셸이면 실제 로그가 안 보일 수 있음)") }
    return @('통과', "$detail, 로그 시작 줄 확인, ${sec}초")
}

function Invoke-Build {
    $t = Get-Date
    & dotnet build (Join-Path $root 'collector-windows') -nologo -v:m *> $buildLog
    $code = $LASTEXITCODE
    $sec = [math]::Round(((Get-Date) - $t).TotalSeconds, 1)
    if ($code -ne 0) {
        $errs = @(Select-String -Path $buildLog -Pattern ': error ' | Select-Object -First 5 | ForEach-Object { $_.Line.Trim() })
        $script:tail = if ($errs.Count) { $errs } else { @(Get-Content $buildLog -Tail 12) }
        Add-Step '빌드' '실패' "종료 코드 $code, ${sec}초 (전체: $buildLog)"
        return $false
    }
    Add-Step '빌드' '통과' "${sec}초"
    if ($SkipTests) { Add-Step '시험' '건너뜀' '-SkipTests' } else { Add-Step '시험' '없음' '수집기에는 시험 프로젝트가 아직 없다' }
    return $true
}

$where = if ($isWorktree) { "워크트리 $(Split-Path $root -Leaf)" } else { '주 체크아웃' }
try {
    if ($Restore) {
        Add-Step '앱 끄기' '통과' (Stop-Collector)
        $stoppedByUs = $true
        Start-AndWait $menuLnk (Join-Path $mainRoot $exeRel) "시작 메뉴 바로가기: $(Split-Path $menuLnk -Leaf)"
    }
    elseif ($NoLaunch) {
        [void](Invoke-Build)
    }
    elseif ($isWorktree) {
        # 워크트리 빌드는 실행 중인 exe를 잠그지 않으므로 먼저 빌드하고, 성공했을 때만 끈다
        if (Invoke-Build) {
            Add-Step '앱 끄기' '통과' (Stop-Collector)
            $stoppedByUs = $true
            $lnk = Join-Path (Split-Path $exe -Parent) 'verify-launch.lnk'
            $sh = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
            $sh.TargetPath = $exe; $sh.Arguments = 'run --tray'; $sh.WorkingDirectory = (Split-Path $exe -Parent); $sh.WindowStyle = 7; $sh.Save()
            Start-AndWait $lnk $exe '워크트리 exe 임시 바로가기(시작 메뉴·자동 실행은 그대로. 되돌리기: -Restore)'
        }
    }
    else {
        # 주 체크아웃: 실행 중인 exe가 bin을 잠그므로 먼저 끈다
        Add-Step '앱 끄기' '통과' (Stop-Collector)
        $stoppedByUs = $true
        if (Invoke-Build) {
            Start-AndWait $menuLnk $exe "시작 메뉴 바로가기: $(Split-Path $menuLnk -Leaf)"
        }
    }
}
catch {
    Add-Step '스크립트' '실패' $_.Exception.Message
}
finally {
    # 끈 수집기는 반드시 다시 켠다(빌드 실패·예외 때)
    if ($stoppedByUs -and -not $launched -and -not (Get-Process -Name $procName -ErrorAction SilentlyContinue)) {
        try {
            Add-Step '다시 켜기' '정보' '이 스크립트가 끈 수집기를 시작 메뉴 바로가기로 다시 켠다'
            Start-AndWait $menuLnk (Join-Path $mainRoot $exeRel) "시작 메뉴 바로가기: $(Split-Path $menuLnk -Leaf)"
        } catch { Add-Step '다시 켜기' '실패' $_.Exception.Message }
    }
}

# 요약
$failed = @($steps | Where-Object State -eq '실패').Count -gt 0
Write-Host ''
Write-Host "==== verify: SecondBrainCollector ($Target, $where) ===="
foreach ($s in $steps) {
    $color = switch ($s.State) { '통과' { 'Green' } '실패' { 'Red' } '주의' { 'Yellow' } default { 'Gray' } }
    Write-Host ("[{0}] {1,-8} {2}" -f $s.State, $s.Name, $s.Detail) -ForegroundColor $color
}
if ($tail.Count) { Write-Host '--- 빌드 오류 ---'; $tail | ForEach-Object { Write-Host "  $_" } }
if (Test-Path $logPath) {
    Write-Host '--- collector.log 끝 6줄 ---'
    Get-Content $logPath -Tail 6 -Encoding UTF8 | ForEach-Object { Write-Host "  $_" }
}
if ($failed) { Write-Host '결과: 실패 (종료 코드 1)' -ForegroundColor Red; exit 1 }
Write-Host '결과: 통과 (종료 코드 0)' -ForegroundColor Green
exit 0
