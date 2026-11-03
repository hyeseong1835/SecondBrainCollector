"""수집기 플러그인 하나를 DB에서 지운다 (관리자). 출력 테이블·뷰와 등록부 행(설치·상태·버전·플러그인)을 모두.

플러그인 id를 바꾸거나 출력 구성을 갈아엎은 뒤, 옛 테이블을 정리할 때 쓴다(예: phone-wifi → phone-network).
기기 롤에는 DDL 권한이 없으므로 관리자 접속으로 돌린다. 테이블에 행이 남아 있으면 멈춘다(--force로 무시).

사용:
  python tools/db/drop-plugin.py phone-wifi --dsn "host=... user=postgres.<ref> password=... sslmode=require"
  LIFELOG_ADMIN_DSN 환경 변수로 --dsn을 대신할 수 있다. 접속 문자열은 파일에 적지 않는다.
  --dry-run: 무엇을 지울지만 보여 준다.
"""
import argparse
import os
import sys

import psycopg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("plugin")
    ap.add_argument("--dsn", default=os.environ.get("LIFELOG_ADMIN_DSN"))
    ap.add_argument("--force", action="store_true", help="행이 남아 있어도 지운다")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if not a.dsn:
        raise SystemExit("--dsn 또는 LIFELOG_ADMIN_DSN이 필요합니다")

    with psycopg.connect(a.dsn) as c, c.cursor() as cur:
        cur.execute("SELECT type FROM lifelog.plugins WHERE id = %s", (a.plugin,))
        row = cur.fetchone()
        if row is None:
            print(f"{a.plugin}: 등록부에 없습니다. 할 일 없음")
            return
        if row[0] != "collector":
            raise SystemExit(f"{a.plugin}은 {row[0]}입니다. 수집기만 지웁니다")
        cur.execute("SELECT output, table_name FROM lifelog.collector_tables WHERE plugin_id = %s ORDER BY table_name", (a.plugin,))
        tables = cur.fetchall()
        left = []
        for output, tbl in tables:
            cur.execute(f'SELECT count(*) FROM lifelog."{tbl}"')
            n = cur.fetchone()[0]
            print(f"  테이블 {tbl}: {n}행")
            if n:
                left.append(tbl)
        outputs = sorted({o for o, _ in tables})
        print(f"  뷰: {', '.join(a.plugin + '/' + o for o in outputs) or '없음'}")
        if left and not a.force:
            raise SystemExit(f"행이 남은 테이블이 있습니다: {left}. 수집기가 옮긴 뒤 다시 하거나 --force")
        if a.dry_run:
            print("--dry-run: 지우지 않았습니다")
            return
        for o in outputs:
            cur.execute(f'DROP VIEW IF EXISTS lifelog."{a.plugin}/{o}"')
        for _, tbl in tables:
            cur.execute(f'DROP TABLE IF EXISTS lifelog."{tbl}"')
        for t in ("collector_tables", "plugin_state", "plugin_installs", "plugin_versions"):
            cur.execute(f"DELETE FROM lifelog.{t} WHERE plugin_id = %s", (a.plugin,))
        cur.execute("DELETE FROM lifelog.plugins WHERE id = %s", (a.plugin,))
        c.commit()
        print(f"{a.plugin}: 테이블 {len(tables)}개·뷰 {len(outputs)}개·등록부 행을 지웠습니다")


if __name__ == "__main__":
    sys.exit(main())
