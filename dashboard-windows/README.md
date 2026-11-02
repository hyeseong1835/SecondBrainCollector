# dashboard-windows

Windows 대시보드 = WPF 창 하나에 WebView2. 화면은 전부 HTML/JS(`web/`)이고, 호스트는 `lifelog` 브리지(`Bridge.cs`)로 DB 질의·등록부·설정만 준다. 설계는 `Documents/새 구조 설계.md` 2.5 "렌더러"(로컬 전용).

```
src/LifeLog.Dashboard.Windows/
  MainWindow.xaml(.cs)   WebView2 준비, 가상 호스트(app.lifelog → web/, plugin-<id>.lifelog → plugins/<id>), 브리지 주입
  Bridge.cs              query(집계 옵션 포함, 플러그인은 inputs만), outputs(등록부), renderers, settings, installPlugin
  web/bridge.js          window.lifelog. 셸은 chrome.webview로, 플러그인 iframe은 부모(셸) 중계로 호스트와 통신
  web/index.html, shell.js  셸: 날짜 이동(day_start 설정), 내장 타임라인(모든 수집기 출력을 공통 열 + 라벨 열로), 렌더러 iframe
```
코어(`LifeLog.Core`)는 수집기와 같은 프로젝트를 참조한다. `Query.cs`가 `lifelog.query`의 SQL 변환(화이트리스트)이다.

## 설정
- `%LOCALAPPDATA%\LifeLog\dashboard\config.json`. 없으면 수집기의 `LifeLog\collector\config.json`을 그대로 읽는다(같은 기기 = 같은 롤, DPAPI는 같은 사용자).
- `settings.json`: `day_start`(기본 `04:00`), `tz`. 기기별.
- 렌더러 플러그인: `%LOCALAPPDATA%\LifeLog\dashboard\plugins\<id>\`. "렌더러 설치" 버튼이 폴더를 골라 복사·등록·전부 허용한다.

## 개발
```bash
dotnet build dashboard-windows
dashboard-windows/src/LifeLog.Dashboard.Windows/bin/Debug/net10.0-windows10.0.19041.0/lifelog-dashboard.exe
# 화면 캡처(개발용): 로드 4초 뒤 PNG로 저장하고 종료
lifelog-dashboard.exe --screenshot out.png
```
로그: `%LOCALAPPDATA%\LifeLog\dashboard\dashboard.log`.

## 렌더러 플러그인 작성
`manifest.json`(`type: renderer`, `runtimes.webview`, `inputs`, `slot`)과 `index.html`. 페이지 안에서 `lifelog.query("win-window/window", { range, limit })`, `lifelog.onRangeChange(fn)`, `lifelog.settings()`. `inputs`에 없는 출력을 질의하면 거부된다. `slot`은 `lane`(타임라인 한 줄, 높이 120px) 또는 `page`(480px).

## 뷰·위젯 (2026-11-02)
설계: `Documents/대시보드 뷰·위젯.md`. 헤더 탭이 뷰, 뷰 안의 카드가 위젯이다. **편집** 버튼으로 위젯 추가(내장 타임라인 + 설치된 `slot: "card"` 렌더러)·삭제·순서·너비(1~3열)·설정(매니페스트 `settings` 선언으로 폼), 뷰 추가·이름·삭제. **설정** 버튼에서 하루 시작과 분류 규칙(`categories`: 이름·app·title 정규식)을 고친다. 전부 이 기기의 `settings.json`에 저장된다.
내장 위젯 6개(`plugins/widget-*`)는 빌드 출력 `bundled-plugins/`에 묶여 첫 실행 때 자동 설치·등록된다. 위젯은 `https://app.lifelog/sdk.js`(저장소 `sdk/lifelog-sdk.js`)를 쓰고 `lifelog.ready((range, widget) => …)`로 시작한다. `lifelog.widget`이 `{ id, w, h, settings }`.
개발용 옵션: `--screenshot 경로 [--wait ms] [--offset -1]`(N일 전 하루를 열어 캡처). `--user-data-dir 경로`는 WebView2 프로필 폴더를 바꾼다(같은 프로필을 쓰는 대시보드가 이미 떠 있으면 브라우저 프로세스를 공유해 새 위젯의 가상 호스트가 안 잡힌다. 개발 중 두 개를 띄울 때). 페이지의 JS 오류는 `dashboard.log`에 `[js …]`로 남는다.

## 플러그인 페이지
헤더의 **플러그인** 버튼. 등록부(`lifelog.registry`)의 수집기·렌더러를 기기별 설치 상태(켜짐/꺼짐·버전·last_seen·미허용 권한)와 테이블·출처와 함께 표로 보여 준다. 이 기기에서 **켜기/끄기**(`setEnabled`), **설치**(GitHub URL·폴더 경로·zip 경로 또는 폴더 고르기, `installPlugin(source)`). 수집기 플러그인은 수집기의 `plugins/`에 넣고 실행 중인 수집기에 reload 신호를 보낸다. 다른 기기에는 설치할 수 없다(파일이 그 기기에 있어야 함). 지금은 권한을 전부 허용하고 켠다.

## 아직 없는 것
- 날짜 외 범위(주·월)와 `query` 집계를 쓰는 내장 화면. 렌더러의 `navigate`.
- 플러그인 권한 항목별 허용 화면, GitHub URL 설치, 렌더러 켜고 끄기 UI.
- 테마 전환(지금은 다크 고정). 겹치는 구간(하루 합계와 시간별)의 표시 구분.
