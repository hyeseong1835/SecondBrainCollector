# collector-windows

Windows 수집기 앱 = 호스트 + 네이티브 소스. 수집기 플러그인은 전부 JS(`plugins/`)다. 설계는 `Documents/수집기 호스트 API.md`(로컬 전용).

```
src/LifeLog.Core/                 공통(대시보드도 쓴다)
  Ids.cs        결정적 id. tools/import/common.py와 바이트 단위로 같다(Python isoformat·json.dumps 재현)
  Manifest.cs   매니페스트 모델. 원문 JSON을 그대로 등록 함수에 보낸다
  Config.cs     %LOCALAPPDATA%\LifeLog\collector\config.json. 기기 롤 비밀번호는 DPAPI
  Db.cs         등록 함수 호출, 스키마 버전 확인, 시계 보정, (device, id) upsert 배치
  Queue.cs      로컬 큐(SQLite). 같은 키의 upsert는 최신 하나만 남긴다
  Uploader.cs   큐 → DB. 실패하면 5초~2분 물러났다 재시도
  SpanManager.cs 구간 관리: open/update/close/put/delete/emit, 1분마다 열린 구간 upsert
src/LifeLog.Collector.Windows/    exe (net10.0-windows)
  Program.cs    setup / install / enable / disable / run / status
  Host.cs       플러그인 등록·로드, 소스 켜기, 주기 작업, 종료
  JsPlugin.cs   Jint 엔진 하나 = 플러그인 하나. ctx(on, every, span, event, state, settings, privacy, has, now, log)
  Dispatcher.cs 모든 JS는 이 스레드에서만
  Sources/      window(SetWinEventHook), idle(GetLastInputInfo), power(SystemEvents), media(GSMTC),
                input(저수준 훅으로 분당 횟수만), battery(GetSystemPowerStatus),
                discord_output(WASAPI 루프백 상시 녹음), discord_bot(디스코드 봇 사람별 녹음, 켰을 때만),
                laptop_mic(노트북 마이크 상시 녹음)
  Sources/Recording/  M4aWriter(ffmpeg로 16kHz 모노 AAC 32kbps .m4a), Segmenter(소리 있는 구간 판정, 폰과 같은 규칙),
                WasapiSegmentCapture(WASAPI 캡처 → Segmenter, 루프백·입력 공용), DiscordSettings·LaptopMicSettings(플러그인 설정)
```

## 쓰는 법
```bash
dotnet build collector-windows
EXE=collector-windows/src/LifeLog.Collector.Windows/bin/Debug/net10.0-windows10.0.19041.0/lifelog-collector.exe
LIFELOG_ADMIN_DSN="Host=...;Port=...;Database=...;Username=...;Password=..." $EXE setup --device laptop --name 노트북 --host H --port P --database DB
$EXE install plugins/win-window --grant-all      # 개발용: 권한 전부 허용하고 켬. 폴더·zip·GitHub URL(https://github.com/소유자/저장소[/tree/브랜치/폴더])
$EXE reload              # 실행 중인 수집기에 플러그인 다시 읽기 요청(install이 자동으로 보낸다)
$EXE run                 # 콘솔. 또는 run --tray (콘솔 숨김, 트레이 아이콘)
$EXE stop                # 정상 종료(열린 구간 닫고 큐 비움)
$EXE autostart on        # 로그온 시 `run --tray` (작업 스케줄러)
```
- Supabase에 붙이기(로컬 Docker DB에서 옮길 때도): 일반 PowerShell에서 `powershell -ExecutionPolicy Bypass -File tools/db/pc-setup.ps1 -ProjectRef <ref>`. 비밀번호를 프롬프트로 받아 마이그레이션 → 수집기·대시보드 끄기 → `setup-reset`(없으면 `setup`) → 수집기 시작 → `-Enable` 플러그인 켜기 → 대시보드 시작. 대시보드는 수집기 설정을 그대로 쓴다.
- `setup`은 관리자 접속으로 `register_device`를 부르고 받은 비밀번호만 저장한다. 관리자 접속 문자열은 저장하지 않는다.
- `install`은 폴더·zip·GitHub URL을 `plugins/<id>`로 복사하고 `register_plugin`을 부른다(`LifeLog.Core/PluginInstaller.cs`, 대시보드와 공용). URL이면 등록부 `plugins.source_url`에 적는다(0006). 실행 중인 수집기가 있으면 reload 신호를 보내 호스트만 새로 만든다(트레이·뮤텍스 유지). `--grant-all`이 없으면 꺼진 채 설치되고 `enable <id>`로 켠다(지금은 권한 전부 허용. 항목별 허용 UI는 대시보드 몫).
- `run`은 DB 코어 스키마 버전이 앱보다 높으면 쓰기를 거부한다. `autostart`는 HKCU Run 키를 쓴다(관리자 권한 불필요).

## 디스코드 녹음 (2026-11-11)
- 상시: `discord-devices`로 렌더 장치를 보고 `discord-device "CABLE Input"`으로 정한다(ID로 저장). 플러그인 `win-discord-output`.
  소리 있는 구간만 `%USERPROFILE%\SecondBrainRecordings\discord-output\날짜\*.m4a`(폰 녹음과 같은 구간 규칙). 열은 phone-audio와 같다.
- 봇: 일반 터미널에서 `discord-token`(토큰·내 사용자 ID를 직접 입력, 화면에 안 보임, DPAPI) → 트레이 "디스코드 봇 녹음" 또는 `discord-bot on`.
  내가 음성 채널에 들어가면 봇(HearingAid와 같은 봇)이 따라 들어가 사람별 파일 `discord-bot\날짜\시각-사용자ID-xxxx.m4a` + 옆 `.json`(표시 이름·서버·채널). 플러그인 `win-discord-bot`.
- AAC 인코딩은 ffmpeg(`SECONDBRAIN_FFMPEG` → `%LOCALAPPDATA%\SecondBrainnalysisfmpeg` → PATH). Media Foundation AAC는 16kHz·32kbps를 못 쓴다.
- `discord-simulate <wav>`: WAV를 상시 녹음 구간 규칙에 흘려 본다(시험용, `%TEMP%\secondbrain-simulate`에 씀).
- 분석: `tools/recordings/analyze.py`가 `discord-output`·`discord-bot`(기기 laptop)을 읽는다. 봇 파일은 세그먼트 speaker = 디스코드 표시 이름.

## 노트북 마이크 녹음 (2026-11-11)
- **디스코드 통화 중일 때만** 마이크를 녹음한다(소리 있는 구간만, 구간 규칙·열은 discord-output·phone-audio와 같다). 플러그인 `win-laptop-mic`, 소스 `laptop_mic`.
  노트북을 들고 다녀 강의실 소리가 잡히던 것을 막으려고 상시 녹음에서 바꿨다(2026-11-11).
  디스코드 출력 녹음(상대 목소리만)과 파일을 따로 둔다: `%USERPROFILE%\SecondBrainRecordings\laptop-mic\날짜\*.m4a`.
- 장치: 설정 `input_device`(ID 또는 이름 일부). 비면 Windows 기본 통신 입력 장치를 쓰고 기본 장치가 바뀌면 다시 붙는다. `mic-devices`로 목록, `mic-device <이름|ID|default>`로 정한다.
- 켜고 끄기: `install plugins/win-laptop-mic --grant-all` / `disable win-laptop-mic` / `enable win-laptop-mic`.
- `mic-test [초] [--device 이름]`: DB 없이 마이크 소스만 돌려 생긴 파일 기록을 찍는다(시험용, `SECONDBRAIN_RECORDINGS`로 위치를 바꿀 수 있다).
- Windows 설정 › 개인 정보 › 마이크 › "데스크톱 앱이 마이크에 액세스하도록 허용"이 꺼져 있으면 무음만 들어온다(파일이 안 생김).

## 디스코드 통화 감지 (2026-11-11, `Sources/Recording/DiscordCall.cs`)
- discord-output·laptop-mic은 디스코드 통화 중일 때만 장치를 연다. 통화 밖에서는 장치를 닫는다(Windows "마이크 사용 중" 표시도 안 뜬다).
- 통화 중 = Discord 프로세스(Discord·DiscordPTB·DiscordCanary)의 Windows 오디오 세션이 활성. 입력(마이크) 세션은 바로, 출력 세션은 2번 연속(약 4초) 보여야 통화로 본다(알림음 거르기). 2초마다 확인, 2번 연속 안 보이면 끝.
- 통화가 끝나면 열린 파일을 바로 닫는다(5초 무음을 기다리지 않음). 통화 시작 직후 앞부분 1초 남짓은 빠질 수 있다.
- 디스코드 봇이 음성 채널에 들어가 녹음 중이면 discord-output·laptop-mic 둘 다 멈춘다. 봇이 나가면 다시 통화 조건대로.
- `discord-call [초]`로 세션과 판정을 볼 수 있다.

## 원시 기능
`ctx.file.list(dir, pattern)`, `ctx.file.read(path, { from, max })` → `{ text, next, size, eof }`, `ctx.file.stat(path)`. 매니페스트의 `file:read:<경로 접두어>`(`~`·`%VAR%` 가능) 아래만 읽는다. `plugins/claude-session`(`~/.claude/projects`의 세션 jsonl)과 `plugins/vscode`(`collector-vscode` 확장이 쓰는 events.jsonl)가 예다. 둘 다 이어 읽기이고 상태에 읽은 위치를 둔다.

## 플러그인 작성
`plugins/win-window/main.js`가 가장 작은 예다. `export default { start(ctx), stop(ctx) }`. 행의 시각은 `Date`(소스 이벤트의 `ev.time`을 그대로 넘기면 된다). 선언되지 않은 열을 넣으면 거부된다. 핸들러 예외는 격리되고 연속 5회면 그 기기에서 꺼진다.

## 아직 없는 것
- 설치 화면의 권한 항목별 허용(지금은 `--grant-all`/`enable`이 전부 허용).
- GitHub URL 설치와 갱신 확인. Lua 런타임. JS 수집기용 원시 기능(`http`, `file`, `sqlite`, `process`, `registry`).
- 소스 이벤트 버퍼(플러그인이 꺼졌다 켜질 때 재전달)와 자원 상한의 메모리 쪽(시간 상한은 있음).
