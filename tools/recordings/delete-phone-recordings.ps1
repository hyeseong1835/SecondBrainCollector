<#
녹음 화면에서 영구 삭제한 녹음의 폰 원본을 adb로 지운다(2026-11-11, 데스크톱이 확인 창에서 "폰 원본도 지우기"를 고른 때만).
  - -Kind phone: /sdcard/Android/data/<Package>/files/recordings/<Rel>   (Rel = "날짜/이름.m4a", pull-recordings.ps1과 같음)
  - -Kind call : /sdcard/Recordings/Call/<이름>                          (pull-call-recordings.ps1과 같음)
  - -Files: 지울 Rel(phone) 또는 이름(call)을 '|'로 이은 한 문자열(-File 실행은 배열 인자를 못 받음). 폴더 밖(..)·절대 경로는 거절한다
  - 가져오기와 같은 잠금을 쓴다(가져오는 중이면 종료 코드 3). 연결 안 됨은 종료 코드 2
-ProgressJson: "@@progress {json}" 줄(result: deleted·missing·failed, end)
종료 코드: 0 성공(없던 파일 포함), 1 지우지 못한 파일 있음, 2 연결 안 됨, 3 가져오는 중.
#>
param([Parameter(Mandatory)][ValidateSet('phone', 'call')] [string] $Kind, [Parameter(Mandatory)] [string] $Files,
      [string] $Package = "dev.hyeseong.secondbrain.collector", [string] $CallRoot = "/sdcard/Recordings/Call", [switch] $ProgressJson)
$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot "pull-common.ps1")
Init-Pull -ProgressJson:$ProgressJson
function Q($s) { "'" + ($s -replace "'", "'\''") + "'" }   # 셸 작은따옴표

$root = if ($Kind -eq 'phone') { "/sdcard/Android/data/$Package/files/recordings" } else { $CallRoot }
Enter-PullLock
Connect-Phone
$done = 0; $missing = 0; $bad = 0
foreach ($f in @($Files -split '\|' | Where-Object { $_ })) {
    $rel = $f.Replace([char]92, [char]47)   # 역슬래시 → /
    if (-not $rel -or -not $rel.EndsWith('.m4a') -or $rel -match '(^|/)\.\.(/|$)' -or $rel.StartsWith('/') -or ($Kind -eq 'call' -and $rel.Contains('/'))) {
        Write-Host "  거절(경로): $f" -ForegroundColor Yellow; $bad++; Progress 'result' @{ name = $f; status = 'failed'; reason = 'path' }; continue
    }
    $path = "$root/$rel"
    $out = & $adb shell "if [ -f $(Q $path) ]; then rm $(Q $path) && echo deleted; else echo missing; fi" 2>&1
    $st = ($out | Select-Object -Last 1)
    if ($st -eq 'deleted') { $done++; Write-Host "  지움: $rel" }
    elseif ($st -eq 'missing') { $missing++; Write-Host "  폰에 없음: $rel" }
    else { $bad++; Write-Host "  지우지 못함: $rel $out" -ForegroundColor Yellow; $st = 'failed' }
    Progress 'result' @{ name = $f; status = $st }
}
Write-Host "폰 원본 지움 $done · 없음 $missing · 실패 $bad"
Progress 'end' @{ kind = $Kind; deleted = $done; missing = $missing; failed = $bad }
if ($bad) { exit 1 }
