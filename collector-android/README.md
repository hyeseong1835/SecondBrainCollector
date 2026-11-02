# collector-android

Android 수집기 앱 = 호스트 + 네이티브 소스. Windows 수집기와 같은 구조로, 수집기 플러그인은 전부 JS(`plugins/`)다. 설계는 `Documents/수집기 호스트 API.md`(로컬 전용). 대상은 갤럭시 S26U(Android 16), minSdk 31.

```
app/src/main/java/dev/hyeseong/lifelog/collector/
  core/
    Ids.kt          결정적 id. collector-windows/Ids.cs·tools/import/common.py와 바이트 단위로 같다
    Manifest.kt     매니페스트 모델(Row, TableMeta 포함). 원문 JSON을 그대로 등록 함수에 보낸다
    Config.kt       EncryptedSharedPreferences(Keystore). 기기 롤 비밀번호는 여기에만. 풀러면 tenant 접미사
    Db.kt           JDBC(pgjdbc). 등록 함수 호출, 스키마 버전 확인, 시계 보정, (device, id) upsert 배치
    Queue.kt        로컬 큐(android SQLite). 같은 키의 upsert는 최신 하나만
    Uploader.kt     큐 → DB. 실패하면 5초~2분 물러났다 재시도
    SpanManager.kt  구간 관리: open/update/close/put/delete/emit, 1분마다 열린 구간 upsert
  js/JsPlugin.kt    QuickJS 컨텍스트 하나 = 플러그인 하나(quickjs-wrapper 3.2, 16KB 정렬). 프렐류드가 ctx를 만든다. 값은 JSON 문자열로 오간다
  sources/
    Sources.kt      screen(브로드캐스트), location(LocationManager 15분), usage(UsageStatsManager 읽기)
    MediaSource.kt  media(MediaSessionManager. 알림 접근 권한 필요)
    NotificationSource.kt notification(NotificationListenerService. 알림 리스너 컴포넌트 MediaListenerService도 여기. posted/removed/snapshot을 extras·랭킹까지 JSON으로)
    HealthSource.kt health(Health Connect 시간 범위 읽기. 지표별로 평탄화만 한다)
  (src/shd|src/noshd)/…/sources/SamsungHealthSource.kt  shealth(삼성 헬스 Data SDK). app/libs/에 aar가 있으면 shd(실제), 없으면 noshd(빈 구현)가 빌드에 들어간다
  CollectorService.kt 포그라운드 서비스 = 호스트. 디스패처(HandlerThread)에서만 JS를 돌린다
  ui/MainActivity.kt  설정·권한·플러그인·실행 한 화면
```

저장소 `plugins/`가 assets로 묶이고 앱을 열 때 `filesDir/plugins/`로 풀린다(phone-*, health만). GitHub URL 설치는 나중.

## 빌드
삼성 헬스 Data SDK를 쓰려면 [삼성 개발자 사이트](https://developer.samsung.com/health/data/overview.html)에서 `samsung-health-data-api-1.1.0.aar`를 받아 `app/libs/`에 둔다(저장소에는 올리지 않는다, .gitignore). 없어도 빌드되며 shealth만 빠진다.
```powershell
$env:JAVA_HOME="C:\Program Files\Android\Android Studio\jbr"
cd collector-android; .\gradlew.bat assembleDebug
# app\build\outputs\apk\debug\app-debug.apk → 폰에 설치(adb install 또는 파일 복사)
```

## 폰에서 설정하는 순서
1. 노트북에서 기기 비밀번호를 새로 만든다(관리자 접속): `SELECT lifelog.reset_device_password('phone');` (폰 기기는 import가 이미 등록했다).
2. 앱 열기 → 기기 id `phone`, 호스트·포트·DB, 테넌트(Supabase 세션 풀러면 프로젝트 ref), 비밀번호 → **저장하고 연결 확인**. 비밀번호는 Keystore로 암호화되어 폰에만 남는다.
3. 권한: **사용 정보 접근**(설정 화면에서 켬), **위치·알림·활동**, **위치 항상 허용**, **Health Connect**(앱별 권한 화면), **알림 접근(미디어·알림)**, **삼성 헬스 SDK**(삼성 헬스 앱 > 설정 > 삼성 헬스 정보 > 버전을 10번 눌러 개발자 모드 > *Developer Mode for Data Read* 켠 뒤 누른다), **배터리 최적화 제외**.
4. **내장 플러그인 전부 등록·켜기**(개발용: 권한 전부 허용. 항목별 허용 UI는 대시보드 몫). 또는 **URL로 설치**: GitHub URL(`https://github.com/소유자/저장소/tree/브랜치/폴더`)을 넣으면 내려받아 `filesDir/plugins/<id>`에 풀고 등록·켠다. 수집 중이면 서비스를 다시 시작한다.
5. **수집 시작**. 알림에 상태가 보인다. 중지는 열린 구간을 닫고 큐를 한 번 비운 뒤 끝난다.

## 소스와 플러그인
| 플러그인 | 소스 | 출력 | 방식 |
|---|---|---|---|
| phone-screen | event:screen | screen(시점, kind on/off/unlock) | 브로드캐스트 |
| phone-location | event:location | point(시점, lat/lon/accuracy) | 15분 간격 균형 전력. 좌표만, 장소 판정 없음 |
| phone-app | read:usage + timer | app(구간, package) | 15분마다 UsageEvents를 읽어 resumed→paused를 구간으로. 1분 미만은 버림 |
| phone-media | event:media | media(구간, package/title/artist/album/duration_ms) | 세션별 재생 중 구간. 곡이 바뀌면 새 구간 |
| phone-notification | event:notification | event(시점, kind posted/removed/snapshot, reason, raw jsonb = 얻을 수 있는 전부), notification(구간, 알림 하나가 떠 있던 동안. package/key/notif_id/channel/category/title/text/sub_text/ongoing/group/importance/posts/removed_reason) | 알림 리스너. `exclude_packages`(기본 수집기 자신), `store_content`(끄면 제목·본문·메시지·사람을 빼고 메타데이터만) |
| health | read:health (+read:shealth) + timer | span(metric/value/category/extra), point(metric/value/unit) | 1시간마다 지난 2일을 다시 읽는다. 같은 레코드는 id가 같아 덮어씀 |

health 지표: steps(시간별 `hourly`)·steps/distance/active_kcal/total_kcal/floors/resting_heart_rate(일별 `daily`)·sleep(구간, extra.stages_min)·sleep_stage(category deep/light/rem/awake)·exercise(category 제목)·heart_rate/spo2/hrv/body_temp/weight(시점). `metrics` 설정으로 고른다(기본 steps, heart_rate, sleep).

삼성 헬스 SDK 지표(`read:shealth`가 있을 때, `shealth_metrics` 설정. 비우면 Health Connect에 없는 것만): energy_score·active_time·sh_active_kcal·sh_total_kcal·sh_distance·sh_steps·sh_floors·water·nutrition_kcal(일별 `daily`), sleep_score·sh_sleep(구간, extra.stages_min/score)·sh_sleep_stage·sh_exercise(category 제목, extra.type/distance_m)·nutrition(category 끼니, extra 영양소), skin_temperature·sh_heart_rate·sh_spo2·blood_pressure(extra diastolic/mean/pulse)·blood_glucose·body_composition(값 kg, extra 체지방 등)·sh_body_temp·sleep_apnea·irregular_heart_rhythm(시점), steps_goal·active_kcal_goal·active_time_goal·water_goal·sleep_goal·nutrition_goal(일별 `goal`). Health Connect와 겹치는 지표는 `sh_`를 붙여 따로 저장한다. SDK 1.1.0에는 **스트레스 타입이 없다**(스트레스 위젯은 비어 있다).

## 휴대폰 대시보드
같은 APK에 아이콘 **LifeLog 대시보드**가 하나 더 생긴다. 컴퓨터 대시보드와 같은 셸·같은 위젯 플러그인을 WebView로 띄우고(위젯은 세로 한 줄), 수집기의 DB 연결을 같이 쓴다. 위젯 배치·설정은 이 폰에만 저장되고, 분류 규칙은 DB(0007)에서 모든 기기가 같이 쓴다.
```
dashboard/DashboardActivity.kt  WebView, 가상 호스트(app.lifelog·plugin-<id>.lifelog), HTML에 bridge.js 주입, 웹 메시지 리스너 LifeLogHost
dashboard/DashboardBridge.kt    렌더러 호스트 API(Windows Bridge.cs와 같은 메서드), 내장 위젯 설치·등록
core/Query.kt                   query → SQL (Windows Query.cs와 같은 규칙). 시험: gradlew testDebugUnitTest --tests *QueryTest* (로컬 Docker DB)
```
개발용: `adb shell am start -n dev.hyeseong.lifelog.collector/.dashboard.DashboardActivity --ei offset -1` (하루 전), `--es page settings`.

## 아직 없는 것
- Health Connect 변경 토큰 읽기(지금은 시간 범위 재읽기).
- QR로 설정 받기.

