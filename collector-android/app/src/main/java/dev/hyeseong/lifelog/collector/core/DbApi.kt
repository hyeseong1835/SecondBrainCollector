package dev.hyeseong.lifelog.collector.core

import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/**
 * [Db]의 API 모드 구현(세컨뇌 DB 이전 7단계). 같은 일을 SecondBrainApi `/api/lifelog/...`로 한다. collector-windows DbApi.cs와 같다.
 * 기기는 서버가 API 키(종류 collector, 이름 = 기기 id)에서 정하므로 여기서 보내는 device는 쓰이지 않는다.
 * 4xx는 IllegalStateException("API 거절"), 5xx·연결 실패는 IOException. 업로더는 둘 다 큐에 남기고 다시 보낸다.
 */
internal class DbApi(baseUrl: String, private val key: String) {
    private val base = baseUrl.trimEnd('/') + "/api/lifelog/"

    private fun e(s: String) = URLEncoder.encode(s, "UTF-8").replace("+", "%20")

    /** 응답 본문(빈 문자열 가능). notFoundIsNull이면 404에 null. */
    private fun send(method: String, path: String, body: String? = null, notFoundIsNull: Boolean = false): String? {
        val c = URL(base + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = 15_000; c.readTimeout = 60_000
            c.setRequestProperty("Authorization", "Bearer ${key.trim()}")
            c.setRequestProperty("User-Agent", "lifelog-collector-android/api")
            c.setRequestProperty("Accept", "application/json")
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = c.responseCode
            if (notFoundIsNull && code == 404) return null
            val text = (if (code >= 400) c.errorStream else c.inputStream)?.use { it.readBytes().toString(Charsets.UTF_8) } ?: ""
            if (code >= 400) {
                val reason = "$code" + (try { " " + JSONObject(text).optString("detail") } catch (_: Exception) { "" })
                if (code < 500) throw IllegalStateException("API 거절: $reason")
                throw IOException("API 오류: $reason")
            }
            return text
        } finally { c.disconnect() }
    }

    private fun obj(method: String, path: String, body: String? = null) = JSONObject(send(method, path, body)!!)
    private fun arr(path: String) = JSONArray(send("GET", path)!!)
    private fun JSONObject.strMap(): Map<String, String> = keys().asSequence().associateWith { getString(it) }

    fun schemaVersion(): Int = obj("GET", "schema").getInt("version")

    fun serverNowMs(): Long = java.time.OffsetDateTime.parse(obj("GET", "now").getString("now")).toInstant().toEpochMilli()

    fun registerPlugin(manifestJson: String): JSONObject = obj("POST", "plugins", manifestJson)

    fun getInstall(plugin: String): Db.Install? {
        val t = send("GET", "installs/${e(plugin)}", notFoundIsNull = true) ?: return null
        val v = JSONObject(t)
        val g = v.getJSONArray("granted"); val set = HashSet<String>(); for (i in 0 until g.length()) set.add(g.getString(i))
        return Db.Install(v.getString("installedVersion"), v.getBoolean("enabled"), v.optJSONObject("settings") ?: JSONObject(), set)
    }

    fun setInstall(plugin: String, version: String, enabled: Boolean, settingsJson: String?, grantedJson: String): JSONObject {
        val body = JSONObject().put("version", version).put("enabled", enabled)
            .put("settings", if (settingsJson == null) JSONObject.NULL else JSONObject(settingsJson)).put("granted", JSONArray(grantedJson))
        return obj("PUT", "installs/${e(plugin)}", body.toString())
    }

    fun setPluginSource(plugin: String, url: String) { send("PUT", "plugins/${e(plugin)}/source", JSONObject().put("url", url).toString()) }

    fun listOutputs(): List<Db.OutputInfo> {
        val a = arr("outputs")
        return (0 until a.length()).map { a.getJSONObject(it) }.map { o ->
            Db.OutputInfo(o.getString("plugin"), o.getString("output"), o.getInt("schemaVersion"), o.getBoolean("isSpan"),
                o.getJSONObject("columns").strMap(), o.optString("label", ""), o.optString("pluginName", o.getString("plugin")))
        }
    }

    fun query(q: QuerySpec): JSONObject {
        val opts = JSONObject().put("range", JSONObject().put("start", q.start.toString()).put("end", q.end.toString()))
            .put("tz", q.tz).put("limit", q.limit)
        q.device?.let { opts.put("device", it) }
        if (q.where.isNotEmpty()) opts.put("where", JSONObject().apply { for ((k, v) in q.where) put(k, JSONArray(v)) })
        if (q.groupTime != null || q.groupBy.isNotEmpty()) opts.put("group", JSONObject().apply {
            q.groupTime?.let { put("time", it) }
            if (q.groupBy.isNotEmpty()) put("by", JSONArray(q.groupBy))
        })
        if (q.agg.isNotEmpty()) opts.put("agg", JSONObject(q.agg as Map<*, *>))
        return obj("POST", "query?table=${e(q.table)}", opts.toString())
    }

    fun registry(): JSONObject = obj("GET", "registry")

    fun categoryRules(): JSONObject? = send("GET", "category-rules")?.takeIf { it.isNotBlank() }?.let { JSONObject(it) }

    fun setCategoryRules(kind: String, rulesJson: String) { send("PUT", "category-rules/${e(kind)}", rulesJson) }

    fun collectorTables(plugin: String): List<TableMeta> {
        val a = arr("plugins/${e(plugin)}/tables")
        return (0 until a.length()).map { a.getJSONObject(it) }.map { t -> TableMeta(t.getString("table"), t.getBoolean("isSpan"), t.getJSONObject("columns").strMap()) }
    }

    fun touch() { send("POST", "touch") }

    fun stateGet(plugin: String, key: String): String? = send("GET", "state/${e(plugin)}/${e(key)}", notFoundIsNull = true)

    fun stateSet(plugin: String, key: String, valueJson: String) { send("PUT", "state/${e(plugin)}/${e(key)}", valueJson) }

    fun upsert(meta: TableMeta, rows: List<Row>) {
        val a = JSONArray()
        for (r in rows) a.put(JSONObject().put("id", r.id).put("start", r.start)
            .put("end", if (meta.isSpan) (r.end ?: r.start) else JSONObject.NULL).put("cols", r.cols))
        send("POST", "rows?table=${e(meta.table)}", JSONObject().put("rows", a).toString())
    }

    fun delete(table: String, id: String) { send("DELETE", "rows?table=${e(table)}&id=${e(id)}") }
}
