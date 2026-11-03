# db

LifeLog 코어 스키마. 설계 근거는 `Documents/`(로컬 전용)의 테이블 설계·등록 함수·인증·권한.

```
migrations/0001_core.sql       스키마 lifelog, 롤(lifelog_devices 그룹, lifelog_reader), 등록부 7개, RLS, updated_at 트리거, current_device()
migrations/0002_functions.sql  register_device, revoke_device, register_plugin, set_install, regen_view, 검증 유틸
migrations/0003_reset_device_password.sql  reset_device_password (관리자. 기기 비밀번호 재발급)
migrations/0004_search_path.sql            SECURITY DEFINER 함수의 search_path 고정
migrations/0005_register_device_existing_role.sql  이미 있는 기기 롤로 다시 등록
migrations/0006_plugin_source.sql          plugins.source_url, set_plugin_source (URL 설치 출처)
migrations/0007_category_rules_platforms.sql  공유 분류 규칙 category_rules·set_category_rules, 매니페스트 platforms·requires(register_plugin을 감쌈)
```

## 규칙
- 파일 이름 `NNNN_이름.sql`, 번호 순으로 적용. 전진만, 다운그레이드 없음. 적용된 버전은 `lifelog.schema_migrations`.
- 적용 주체는 앱(수집기·대시보드)이다. `tools/db/migrate.py`는 같은 규칙의 개발용 실행기.
- 앱이 아는 버전보다 DB가 높으면 앱은 쓰기를 멈춘다.
- 모든 객체는 마이그레이션을 실행한 롤이 소유한다(= 관리자). 기기 롤은 DDL 권한이 없고 등록 함수(`SECURITY DEFINER`)를 통해서만 테이블을 얻는다.
- 테이블·뷰 이름은 매니페스트 표기 그대로: `"win-window/window@1"`, 뷰 `"win-window/window"`. 항상 큰따옴표.

## 로컬 테스트
```bash
docker run -d --name lifelog-pg -e POSTGRES_PASSWORD=lifelog -e POSTGRES_DB=lifelog -p 54329:5432 postgres:16
python tools/db/migrate.py --dsn postgresql://postgres:lifelog@localhost:54329/lifelog
python tools/db/smoke.py   --dsn postgresql://postgres:lifelog@localhost:54329/lifelog
```
`tools/db/drop-plugin.py <id>`(또는 비밀번호를 프롬프트로 받는 `drop-plugin.ps1 -ProjectRef … -Plugin <id>`)는 수집기 플러그인 하나의 테이블·뷰·등록부 행을 지운다(관리자). 행이 남아 있으면 멈춘다. 2026-11-03 phone-wifi → phone-network: 폰 수집기가 phone-wifi 행을 phone-network/wifi로 옮기고 지운 뒤 `drop-plugin.ps1 -Plugin phone-wifi`로 정리한다.

`smoke.py`는 기기 등록 → 기기 롤로 수집기 등록(거부 사례 포함) → 행 쓰기와 RLS → 열 집합 v2와 뷰 → `set_install` → 렌더러 미충족 입력 → 기기 폐기를 밟는다. 끝에 `전부 통과`가 나와야 한다.

## 주의
- `lifelog_reader`는 LOGIN 롤로 만들지만 비밀번호가 없다. 관리자가 `ALTER ROLE lifelog_reader PASSWORD '...'`로 준다.
- 기기 롤 비밀번호는 `register_device`의 반환값으로 한 번만 나온다. base64라 `/`·`+`가 섞이므로 접속 문자열은 URL이 아니라 키워드 형식(`host=... user=... password=...`)으로 만든다.
- Supabase에서는 풀러 세션 모드 주소로 접속한다(직접 연결은 IPv6 전용).
