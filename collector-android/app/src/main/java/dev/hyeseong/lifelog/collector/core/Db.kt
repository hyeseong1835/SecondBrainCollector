package dev.hyeseong.lifelog.collector.core

import org.json.JSONArray
import org.json.JSONObject
import java.sql.Connection
import java.sql.DriverManager
import java.sql.Timestamp
import java.sql.Types
import java.time.Instant
import java.util.UUID

/**
 * DB 접근(JDBC). 기기 롤로 붙는다. 연결은 하나를 유지하고 끊기면 다시 연다. 호출은 업로더·디스패처 스레드에서.
 * 설정에 API 주소가 있으면(SecondBrain DB 이전 7단계) DB에 붙지 않고 [DbApi]로 SecondBrainApi를 거친다.
 */
class Db(private val cfg: Config) : AutoCloseable {
    companion object {
        /** 이 앱이 아는 코어 스키마 버전. DB가 더 높으면 쓰기를 멈춘다. */
        const val KNOWN_SCHEMA_VERSION = 7
        /** 수집기가 돌 수 있는 가장 낮은 코어 스키마. 0007(공유 규칙·platforms)은 없어도 수집은 된다 */
        const val MIN_SCHEMA_VERSION = 6
        /** 0007부터: 공유 분류 규칙, 매니페스트 platforms·requires */
        const val RULES_SCHEMA_VERSION = 7
        init { Class.forName("org.postgresql.Driver") }
    }

    private var conn: Connection? = null
    private val api: DbApi? = if (cfg.usesApi) DbApi(cfg.apiUrl, cfg.apiKey) else null
    val usesApi get() = api != null
    var clockOffsetMs = 0L; private set

    @Synchronized
    fun connection(): Connection {
        conn?.let { if (!it.isClosed && it.isValid(5)) return it }
        val c = DriverManager.getConnection(cfg.jdbcUrl(), cfg.user, cfg.password)
        c.autoCommit = true
        conn = c
        return c
    }

    private var schema: Int? = null
    fun schemaVersion(): Int = api?.schemaVersion()?.also { schema = it } ?: connection().createStatement().use { st ->
        st.executeQuery("SELECT COALESCE(max(version), 0) FROM lifelog.schema_migrations").use { r -> r.next(); r.getInt(1) }
    }.also { schema = it }
    fun cachedSchema(): Int = schema ?: schemaVersion()

    fun syncClock() {
        val before = System.currentTimeMillis()
        val dbNow = api?.serverNowMs() ?: connection().createStatement().use { st -> st.executeQuery("SELECT now()").use { r -> r.next(); r.getTimestamp(1).time } }
        val after = System.currentTimeMillis()
        clockOffsetMs = dbNow - (before + after) / 2
    }

    fun now(): Instant = Instant.ofEpochMilli(System.currentTimeMillis() + clockOffsetMs)

    fun registerPlugin(m: Manifest, device: String): JSONObject = api?.registerPlugin(m.raw) ?: connection().prepareStatement("SELECT lifelog.register_plugin(?::jsonb, ?)").use { ps ->
        // 0006 DB는 platforms·requires를 모른다. 두 키를 빼고 등록하고, 0007이 적용되면 같은 버전으로 다시 등록할 때 채워진다
        val raw = if (cachedSchema() < RULES_SCHEMA_VERSION) JSONObject(m.raw).apply { remove("platforms"); remove("requires") }.toString() else m.raw
        ps.setString(1, raw); ps.setString(2, device)
        ps.executeQuery().use { r -> r.next(); JSONObject(r.getString(1)) }
    }

    class Install(val installedVersion: String, val enabled: Boolean, val settings: JSONObject, val granted: Set<String>)

    fun getInstall(plugin: String, device: String): Install? = if (api != null) api.getInstall(plugin) else connection().prepareStatement(
        "SELECT installed_version, enabled, settings::text, granted::text FROM lifelog.plugin_installs WHERE plugin_id = ? AND device = ?").use { ps ->
        ps.setString(1, plugin); ps.setString(2, device)
        ps.executeQuery().use { r ->
            if (!r.next()) return null
            val g = JSONArray(r.getString(4)); val set = HashSet<String>(); for (i in 0 until g.length()) set.add(g.getString(i))
            Install(r.getString(1), r.getBoolean(2), JSONObject(r.getString(3)), set)
        }
    }

    fun setInstall(plugin: String, device: String, version: String, enabled: Boolean, settingsJson: String?, grantedJson: String): JSONObject =
        api?.setInstall(plugin, version, enabled, settingsJson, grantedJson) ?: connection().prepareStatement("SELECT lifelog.set_install(?, ?, ?, ?, ?::jsonb, ?::jsonb)").use { ps ->
            ps.setString(1, plugin); ps.setString(2, device); ps.setString(3, version); ps.setBoolean(4, enabled)
            if (settingsJson == null) ps.setNull(5, Types.OTHER) else ps.setString(5, settingsJson)
            ps.setString(6, grantedJson)
            ps.executeQuery().use { r -> r.next(); JSONObject(r.getString(1)) }
        }

    /** plugins.source_url(0006). URL로 설치했을 때 적는다. */
    fun setPluginSource(plugin: String, url: String) { if (api != null) api.setPluginSource(plugin, url) else connection().prepareStatement("SELECT lifelog.set_plugin_source(?, ?)").use { ps -> ps.setString(1, plugin); ps.setString(2, url); ps.executeQuery().close() } }

    // ---- 대시보드(렌더러 호스트)용 ----

    class OutputInfo(val plugin: String, val output: String, val schemaVersion: Int, val isSpan: Boolean, val columns: Map<String, String>, val label: String, val pluginName: String) {
        val view get() = "$plugin/$output"
    }

    /** 등록부의 수집기 출력 목록(테이블 메타 + 매니페스트 라벨 열). collector-windows Db.ListOutputsAsync와 같다. */
    fun listOutputs(): List<OutputInfo> = api?.listOutputs() ?: connection().createStatement().use { st ->
        st.executeQuery("SELECT ct.plugin_id, ct.output, ct.schema_version, ct.kind, ct.columns::text, pv.manifest::text " +
            "FROM lifelog.collector_tables ct JOIN lifelog.plugins p ON p.id = ct.plugin_id " +
            "JOIN lifelog.plugin_versions pv ON pv.plugin_id = p.id AND pv.version = p.latest_version ORDER BY ct.plugin_id, ct.output, ct.schema_version").use { r ->
            val out = ArrayList<OutputInfo>()
            while (r.next()) {
                val cols = JSONObject(r.getString(5)).let { c -> c.keys().asSequence().associateWith { c.getString(it) } }
                val man = JSONObject(r.getString(6))
                val label = man.optJSONArray("outputs")?.let { a -> (0 until a.length()).map { a.getJSONObject(it) }.firstOrNull { it.getString("name") == r.getString(2) }?.optString("label") } ?: ""
                out += OutputInfo(r.getString(1), r.getString(2), r.getInt(3), r.getString(4) == "span", cols, label, man.optString("name", r.getString(1)))
            }
            out
        }
    }

    /** query 실행. 결과 { rows: [...], truncated }. 시각은 ISO(UTC), uuid는 문자열, jsonb는 문자열(Windows 호스트와 같다). */
    fun query(spec: QuerySpec, meta: TableMeta): JSONObject = api?.query(spec) ?: runQuery(connection(), spec, meta)

    /** 등록부 전체(기기·플러그인·설치·테이블). collector-windows Db.ListRegistryAsync와 같은 SQL. */
    fun listRegistry(): JSONObject = api?.registry() ?: connection().createStatement().use { st ->
        st.executeQuery("""SELECT json_build_object(
  'devices', (SELECT COALESCE(json_agg(json_build_object('id', id, 'name', name, 'platform', platform, 'last_seen', last_seen, 'revoked', revoked_at IS NOT NULL) ORDER BY id), '[]'::json) FROM lifelog.devices),
  'plugins', (SELECT COALESCE(json_agg(json_build_object(
      'id', p.id, 'type', p.type, 'latest_version', p.latest_version, 'source_url', p.source_url,
      'name', (SELECT v.manifest->>'name' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'platforms', (SELECT v.manifest->'platforms' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'requires', (SELECT v.manifest->'requires' FROM lifelog.plugin_versions v WHERE v.plugin_id = p.id AND v.version = p.latest_version),
      'installs', (SELECT COALESCE(json_agg(json_build_object('device', i.device, 'version', i.installed_version, 'enabled', i.enabled, 'granted', i.granted, 'unmet', i.unmet, 'last_seen', i.last_seen) ORDER BY i.device), '[]'::json) FROM lifelog.plugin_installs i WHERE i.plugin_id = p.id),
      'tables', (SELECT COALESCE(json_agg(t.table_name ORDER BY t.table_name), '[]'::json) FROM lifelog.collector_tables t WHERE t.plugin_id = p.id)
    ) ORDER BY p.id), '[]'::json) FROM lifelog.plugins p))::text""").use { r -> r.next(); JSONObject(r.getString(1)) }
    }

    /** 공유 분류 규칙(0007) { pc, phone }. 0006 DB면 null(호출자가 로컬 파일을 쓴다). */
    fun categoryRules(): JSONObject? {
        if (api != null) return api.categoryRules()
        if (cachedSchema() < RULES_SCHEMA_VERSION) return null
        return connection().createStatement().use { st ->
            st.executeQuery("SELECT json_build_object('pc', (SELECT rules FROM lifelog.category_rules WHERE kind = 'pc'), 'phone', (SELECT rules FROM lifelog.category_rules WHERE kind = 'phone'))::text")
                .use { r -> r.next(); JSONObject(r.getString(1)) }
        }
    }

    fun setCategoryRules(kind: String, rulesJson: String) {
        if (api != null) { api.setCategoryRules(kind, rulesJson); return }
        connection().prepareStatement("SELECT lifelog.set_category_rules(?, ?::jsonb)").use { ps -> ps.setString(1, kind); ps.setString(2, rulesJson); ps.executeQuery().close() }
    }

    /** 정각에 맞지 않는 시간별 걸음 행(옛 HealthSource 버그로 겹쳐 쌓인 것)을 지운다. 지운 행 수. API 모드에는 없다(옮길 때 이미 정리됨) → 0. */
    fun deleteMisalignedHourlySteps(device: String): Int = if (api != null) 0 else connection().prepareStatement(
        "DELETE FROM lifelog.\"health/span@1\" WHERE device = ? AND metric = 'steps' AND category = 'hourly' AND date_trunc('hour', start) <> start").use { ps ->
        ps.setString(1, device); ps.executeUpdate()
    }

    /**
     * phone-wifi → phone-network 옮기기(2026-11-03). 이 기기의 phone-wifi/connection 행을 phone-network/wifi로 같은 id로 복사하고(rssi는 버린다),
     * phone-wifi의 두 테이블에서 이 기기 행을 지운다. 테이블 자체는 관리자가 tools/db/drop-plugin.py로 지운다. 옮긴 행 수, phone-wifi가 없으면 -1.
     */
    fun migrateWifiToNetwork(device: String): Int {
        if (api != null) return -1   // API 모드에는 없다(옮길 때 이미 정리됨)
        val c = connection()
        val has = c.prepareStatement("SELECT count(*) FROM lifelog.collector_tables WHERE table_name IN ('phone-wifi/connection@1', 'phone-network/wifi@1')").use { ps ->
            ps.executeQuery().use { r -> r.next(); r.getInt(1) }
        }
        if (has < 2) return -1
        c.autoCommit = false
        try {
            val cols = "network, ssid, bssid, frequency_mhz, band, standard, security, link_speed_mbps, validated, metered, ip, gateway, roams"
            val n = c.prepareStatement("INSERT INTO lifelog.\"phone-network/wifi@1\" (device, id, start, \"end\", $cols) " +
                "SELECT device, id, start, \"end\", $cols FROM lifelog.\"phone-wifi/connection@1\" WHERE device = ? ON CONFLICT (device, id) DO NOTHING").use { ps -> ps.setString(1, device); ps.executeUpdate() }
            for (t in listOf("phone-wifi/connection@1", "phone-wifi/event@1"))
                c.prepareStatement("DELETE FROM lifelog.\"$t\" WHERE device = ?").use { ps -> ps.setString(1, device); ps.executeUpdate() }
            c.commit()
            return n
        } catch (e: Exception) { c.rollback(); throw e } finally { c.autoCommit = true }
    }

    /** 플러그인의 모든 출력 테이블(옛 schema_version 포함). 출력을 올린 뒤 큐에 남은 옛 버전 행을 올릴 때 쓴다. */
    fun collectorTables(plugin: String): List<TableMeta> = api?.collectorTables(plugin) ?: connection().prepareStatement(
        "SELECT table_name, kind, columns::text FROM lifelog.collector_tables WHERE plugin_id = ?").use { ps ->
        ps.setString(1, plugin)
        ps.executeQuery().use { r ->
            val out = ArrayList<TableMeta>()
            while (r.next()) { val c = JSONObject(r.getString(3)); out.add(TableMeta(r.getString(1), r.getString(2) == "span", c.keys().asSequence().associateWith { c.getString(it) })) }
            out
        }
    }

    fun touchLastSeen(device: String) {
        if (api != null) { api.touch(); return }
        connection().prepareStatement("UPDATE lifelog.devices SET last_seen = now() WHERE id = ?").use { ps -> ps.setString(1, device); ps.executeUpdate() }
    }

    fun stateGet(plugin: String, device: String, key: String): String? = if (api != null) api.stateGet(plugin, key) else connection().prepareStatement(
        "SELECT value::text FROM lifelog.plugin_state WHERE plugin_id = ? AND device = ? AND key = ?").use { ps ->
        ps.setString(1, plugin); ps.setString(2, device); ps.setString(3, key)
        ps.executeQuery().use { r -> if (r.next()) r.getString(1) else null }
    }

    fun stateSet(plugin: String, device: String, key: String, valueJson: String) { if (api != null) { api.stateSet(plugin, key, valueJson); return }; connection().prepareStatement(
        "INSERT INTO lifelog.plugin_state (plugin_id, device, key, value) VALUES (?, ?, ?, ?::jsonb) ON CONFLICT (plugin_id, device, key) DO UPDATE SET value = EXCLUDED.value").use { ps ->
        ps.setString(1, plugin); ps.setString(2, device); ps.setString(3, key); ps.setString(4, valueJson); ps.executeUpdate()
    } }

    /** Android의 java.sql.Timestamp에는 from(Instant)이 없다 */
    private fun ts(iso: String): Timestamp { val i = Instant.parse(iso); return Timestamp(i.toEpochMilli()).also { it.nanos = i.nano } }
    private fun q(id: String) = "\"" + id.replace("\"", "\"\"") + "\""

    /** 한 테이블의 행 여러 개를 (device, id) upsert. 한 트랜잭션. */
    fun upsert(meta: TableMeta, rows: List<Row>) {
        if (rows.isEmpty()) return
        if (api != null) { api.upsert(meta, rows); return }
        val cols = meta.columns.keys.sorted()
        val timeCols = if (meta.isSpan) "start, \"end\"" else "time"
        val colList = cols.joinToString(", ") { q(it) }
        val updates = (if (meta.isSpan) listOf("\"end\" = EXCLUDED.\"end\"") else emptyList<String>()) + cols.map { "${q(it)} = EXCLUDED.${q(it)}" }
        val n = 2 + (if (meta.isSpan) 2 else 1) + cols.size
        val sql = "INSERT INTO lifelog.${q(meta.table)} (device, id, $timeCols${if (cols.isEmpty()) "" else ", $colList"}) VALUES (${List(n) { "?" }.joinToString(", ")}) " +
            "ON CONFLICT (device, id) DO UPDATE SET ${updates.joinToString(", ")}"
        val c = connection()
        c.autoCommit = false
        try {
            c.prepareStatement(sql).use { ps ->
                for (r in rows) {
                    var i = 1
                    ps.setString(i++, r.device)
                    ps.setObject(i++, UUID.fromString(r.id))
                    ps.setTimestamp(i++, ts(r.start))
                    if (meta.isSpan) ps.setTimestamp(i++, ts(r.end ?: r.start))
                    for (col in cols) setParam(ps, i++, meta.columns.getValue(col), r.cols.opt(col))
                    ps.addBatch()
                }
                ps.executeBatch()
            }
            c.commit()
        } catch (e: Exception) { c.rollback(); throw e } finally { c.autoCommit = true }
    }

    fun delete(table: String, device: String, id: String) {
        if (api != null) { api.delete(table, id); return }
        connection().prepareStatement("DELETE FROM lifelog.${q(table)} WHERE device = ? AND id = ?").use { ps -> ps.setString(1, device); ps.setObject(2, UUID.fromString(id)); ps.executeUpdate() }
    }

    private fun setParam(ps: java.sql.PreparedStatement, i: Int, type: String, v: Any?) {
        val isNull = v == null || v == JSONObject.NULL
        when (type) {
            "text" -> if (isNull) ps.setNull(i, Types.VARCHAR) else ps.setString(i, v.toString())
            "boolean" -> if (isNull) ps.setNull(i, Types.BOOLEAN) else ps.setBoolean(i, v as Boolean)
            "integer" -> if (isNull) ps.setNull(i, Types.INTEGER) else ps.setInt(i, (v as Number).toInt())
            "bigint" -> if (isNull) ps.setNull(i, Types.BIGINT) else ps.setLong(i, (v as Number).toLong())
            "double precision" -> if (isNull) ps.setNull(i, Types.DOUBLE) else ps.setDouble(i, (v as Number).toDouble())
            "numeric" -> if (isNull) ps.setNull(i, Types.NUMERIC) else ps.setBigDecimal(i, java.math.BigDecimal(v.toString()))
            "timestamptz" -> if (isNull) ps.setNull(i, Types.TIMESTAMP) else ps.setTimestamp(i, ts(v.toString()))
            "date" -> if (isNull) ps.setNull(i, Types.DATE) else ps.setDate(i, java.sql.Date.valueOf(v.toString()))
            "jsonb" -> { val o = org.postgresql.util.PGobject(); o.type = "jsonb"; o.value = if (isNull) null else v.toString(); ps.setObject(i, o) }
            "text[]" -> if (isNull) ps.setNull(i, Types.ARRAY) else {
                val a = v as JSONArray; ps.setArray(i, ps.connection.createArrayOf("text", Array(a.length()) { a.getString(it) }))
            }
            else -> throw IllegalStateException("모르는 열 타입: $type")
        }
    }

    override fun close() { try { conn?.close() } catch (_: Exception) {} }
}
