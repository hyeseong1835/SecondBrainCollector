package dev.hyeseong.lifelog.collector

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import dev.hyeseong.lifelog.collector.core.*
import dev.hyeseong.lifelog.collector.js.JsPlugin
import dev.hyeseong.lifelog.collector.sources.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.time.Instant
import java.util.UUID

/**
 * 수집기 호스트 = 포그라운드 서비스. 플러그인 등록·로드, 소스 켜기, 주기 작업(열린 구간 upsert 1분, last_seen 5분), 업로더.
 * 모든 JS는 디스패처(HandlerThread)에서만 돈다. 소스는 거기로 post한다.
 */
class CollectorService : Service() {
    companion object {
        const val CHANNEL = "lifelog"
        @Volatile var running = false; private set
        @Volatile var status = "꺼짐"; private set
        fun log(ctx: android.content.Context, msg: String) {
            val line = "${java.time.LocalDateTime.now().withNano(0)} $msg\n"
            android.util.Log.i("LifeLog", msg)
            try { Config.logFile(ctx).appendText(line) } catch (_: Exception) {}
        }
    }

    private lateinit var cfg: Config
    private lateinit var db: Db
    private lateinit var queue: Queue
    private lateinit var spans: SpanManager
    private val thread = HandlerThread("lifelog-dispatcher").apply { start() }
    private val handler by lazy { Handler(thread.looper) }
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val plugins = ArrayList<JsPlugin>()
    private val installs = HashMap<String, Db.Install>()
    private val tables = HashMap<String, TableMeta>()
    private val eventSources = HashMap<String, EventSource>()
    private val readSources = HashMap<String, ReadSource>()
    private var uploadJob: Job? = null
    private val tickers = ArrayList<Runnable>()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (running) { log(this, "이미 실행 중, 시작 요청 무시"); return START_STICKY }
        // 포그라운드 종류는 실제로 허용된 런타임 권한에 맞춘다(없는 권한의 종류를 쓰면 SecurityException)
        fun granted(p: String) = checkSelfPermission(p) == android.content.pm.PackageManager.PERMISSION_GRANTED
        var type = 0
        if (granted(android.Manifest.permission.ACCESS_FINE_LOCATION) || granted(android.Manifest.permission.ACCESS_COARSE_LOCATION)) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
        if (granted(android.Manifest.permission.ACTIVITY_RECOGNITION)) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_HEALTH
        if (type == 0) type = ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        try { startForeground(1, notification("시작 중"), type) }
        catch (e: Exception) {
            // 백그라운드(부팅)에서 location/health 종류가 거부되면 specialUse로 한 번 더
            log(this, "포그라운드 종류 $type 거부, specialUse로 재시도: ${e.message}")
            startForeground(1, notification("시작 중"), ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        }
        running = true
        scope.launch { try { boot() } catch (e: Exception) { log(this@CollectorService, "시작 실패: $e"); status = "시작 실패: ${e.message}"; stopSelf() } }
        return START_STICKY
    }

    private fun boot() {
        cfg = Config(this)
        if (!cfg.isComplete) throw IllegalStateException("설정이 없습니다")
        db = Db(cfg)
        val ver = db.schemaVersion()
        if (ver > Db.KNOWN_SCHEMA_VERSION) throw IllegalStateException("DB 코어 스키마($ver)가 이 앱(${Db.KNOWN_SCHEMA_VERSION})보다 높습니다. 앱을 갱신하세요")
        if (ver < Db.MIN_SCHEMA_VERSION) throw IllegalStateException("DB 코어 스키마가 $ver 입니다. 마이그레이션을 먼저 적용하세요")
        if (!cfg.done("steps_hourly_v1")) {   // 2026-11-03: 겹쳐 쌓인 시간별 걸음 행 정리(health 플러그인이 30일을 다시 읽는다)
            try { log(this, "시간별 걸음 정리: ${db.deleteMisalignedHourlySteps(cfg.device)}행 지움"); cfg.markDone("steps_hourly_v1") }
            catch (e: Exception) { log(this, "시간별 걸음 정리 실패: ${e.message}") }
        }
        db.syncClock()
        db.touchLastSeen(cfg.device)
        queue = Queue(Config.queuePath(this))
        spans = SpanManager(queue, cfg.device)
        log(this, "DB 연결. 시계 보정 ${db.clockOffsetMs}ms, 큐 대기 ${queue.count()}건")

        readSources["usage"] = UsageSource(this)
        readSources["health"] = HealthSource(this)
        readSources["shealth"] = SamsungHealthSource(this)
        handler.post {
            loadAndStart()
            // 업로더는 플러그인 등록으로 테이블 메타가 채워진 뒤에 돌린다(큐에 남은 행의 메타를 못 찾는 일이 없게)
            uploadJob = scope.launch { Uploader(queue, db, { tables[it] }) { log(this@CollectorService, it) }.run() }
        }
    }

    /** 플러그인 로드·시작·소스 켜기. QuickJs 인스턴스는 만든 스레드(디스패처)에서만 쓴다. */
    private fun loadAndStart() {
        val dir = Config.pluginsDir(this)
        if (dir.isDirectory) for (pdir in dir.listFiles()!!.filter { File(it, "manifest.json").exists() }) {
            try {
                val m = Manifest.load(pdir)
                if (m.type != "collector") continue
                val r = db.registerPlugin(m, cfg.device)
                val created = r.optJSONArray("created_tables")
                if (created != null && created.length() > 0) log(this, "[${m.id}] 테이블 생성: $created")
                for (o in m.outputs) tables[m.tableName(o)] = TableMeta(m.tableName(o), o.isSpan, o.columns)
                val inst = db.getInstall(m.id, cfg.device) ?: continue
                installs[m.id] = inst
                if (!inst.enabled) { log(this, "[${m.id}] 꺼져 있음"); continue }
                // 필수 소스가 이 호스트에 없으면 못 돌린다. 선택 소스(예: read:shealth)는 has()로 플러그인이 확인한다
                val missing = m.capabilities.filter { it.second }.map { it.first }.filter { it.startsWith("event:") || it.startsWith("read:") }
                    .map { it.substringAfter(':') }.filter { it !in setOf("screen", "location", "media", "notification", "wifi", "usage", "health", "shealth") }
                if (missing.isNotEmpty()) { log(this, "[${m.id}] 이 호스트에 없는 필수 소스: $missing"); continue }
                val p = JsPlugin(m, pdir, HostImpl(m))
                p.load()
                plugins.add(p)
            } catch (e: Exception) { log(this, "플러그인 로드 실패 ${pdir.name}: $e") }
        }
        if (plugins.isEmpty()) log(this, "켜진 플러그인이 없습니다")

        for (p in plugins) try { p.start(); log(this, "[${p.manifest.id}] 시작") } catch (e: Exception) { log(this, "[${p.manifest.id}] start 실패: $e") }
        for (src in plugins.flatMap { it.subscribed }.distinct()) {
            val s: EventSource = when (src) { "screen" -> ScreenSource(this); "location" -> LocationSource(this); "media" -> MediaSource(this); "notification" -> NotificationSource(this); "wifi" -> WifiSource(this); else -> continue }
            try {
                s.start { ev -> handler.post { for (p in plugins) p.deliver(src, ev.toString()) } }
                eventSources[src] = s
                log(this, "소스 켬: $src")
            } catch (e: Exception) { log(this, "소스 $src 실패: ${e.message}") }
        }
        tick(60_000L) { spans.tick(db.now()) }
        tick(300_000L) { scope.launch { try { db.touchLastSeen(cfg.device) } catch (_: Exception) {} } }
        status = "수집 중 · 플러그인 ${plugins.size}개"
        getSystemService(NotificationManager::class.java).notify(1, notification(status))
    }

    private fun tick(period: Long, body: () -> Unit) {
        val r = object : Runnable { override fun run() { try { body() } catch (e: Exception) { log(this@CollectorService, "주기 작업 실패: $e") }; handler.postDelayed(this, period) } }
        tickers.add(r); handler.postDelayed(r, period)
    }

    override fun onDestroy() {
        running = false
        status = "종료 중"
        for (s in eventSources.values) try { s.close() } catch (_: Exception) {}
        val done = java.util.concurrent.CountDownLatch(1)
        handler.post {
            for (r in tickers) handler.removeCallbacks(r)
            for (p in plugins) p.stop()
            if (::spans.isInitialized) spans.closeAll(db.now())
            for (p in plugins) p.close()
            done.countDown()
        }
        done.await(5, java.util.concurrent.TimeUnit.SECONDS)
        // 진행 중인 전송 배치가 끝나길 기다린 뒤 업로더를 멈춘다(DB를 먼저 닫으면 "connection closed"로 실패 로그가 남는다)
        try { Thread.sleep(1000) } catch (_: Exception) {}
        uploadJob?.let { j: Job -> j.cancel(); kotlinx.coroutines.runBlocking { kotlinx.coroutines.withTimeoutOrNull<Unit>(4000) { j.join() } } }
        if (::queue.isInitialized) { log(this, "종료. 큐 남은 건수 ${queue.count()}"); queue.close() }
        if (::db.isInitialized) db.close()
        thread.quitSafely()
        status = "꺼짐"
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val nm = getSystemService(NotificationManager::class.java)
        if (nm.getNotificationChannel(CHANNEL) == null) nm.createNotificationChannel(NotificationChannel(CHANNEL, "LifeLog 수집기", NotificationManager.IMPORTANCE_LOW))
        return Notification.Builder(this, CHANNEL).setContentTitle("LifeLog 수집기").setContentText(text).setSmallIcon(android.R.drawable.ic_menu_recent_history).setOngoing(true).build()
    }

    // ---- JS → 호스트. 디스패처 스레드에서 불린다 ----
    private inner class HostImpl(private val m: Manifest) : JsPlugin.HostApi {
        private fun plugin() = plugins.first { it.manifest.id == m.id }
        private fun inst() = installs.getValue(m.id)
        private fun requireCap(c: String) { if (c !in inst().granted) throw IllegalStateException("권한이 허용되지 않았습니다: $c") }
        private fun output(name: String) = m.output(name) ?: throw IllegalStateException("없는 출력: $name")
        private fun parseRow(o: Manifest.Output, json: String): Triple<JSONObject, Instant?, Instant?> {
            val all = JSONObject(json)
            var start: Instant? = null; var end: Instant? = null
            if (all.has("start")) { start = Instant.parse(all.getString("start")); all.remove("start") }
            if (all.has("time")) { start = Instant.parse(all.getString("time")); all.remove("time") }
            if (all.has("end") && !all.isNull("end")) { end = Instant.parse(all.getString("end")); all.remove("end") } else all.remove("end")
            val unknown = all.keys().asSequence().filter { it !in o.columns }.toList()
            if (unknown.isNotEmpty()) throw IllegalStateException("${o.name}: 선언되지 않은 열 $unknown")
            return Triple(all, start, end)
        }

        override fun subscribe(source: String) { requireCap("event:$source"); plugin().subscribed.add(source) }
        override fun every(interval: String, timerIndex: Int) {
            requireCap("timer")
            val ms = parseInterval(interval)
            val p = plugin()
            tick(ms) { p.tick(timerIndex) }
        }
        override fun read(source: String, argsJson: String): String {
            requireCap("read:$source")
            val s = readSources[source] ?: throw IllegalStateException("없는 소스: $source")
            // Error(NoClassDefFoundError 등)는 JS 브리지가 잡지 못해 서비스가 죽는다. 플러그인이 catch할 수 있는 예외로 바꾼다
            return try { s.read(JSONObject(argsJson)) } catch (e: Exception) { throw e } catch (e: Throwable) { throw IllegalStateException("$source: ${e.javaClass.simpleName}: ${e.message}", e) }
        }
        override fun spanOpen(output: String, rowJson: String): String {
            val o = output(output); val (cols, start, _) = parseRow(o, rowJson)
            return spans.open(m, o, start ?: throw IllegalStateException("$output: start가 없습니다"), cols).toString()
        }
        override fun spanUpdate(id: String, patchJson: String) = spans.update(UUID.fromString(id), db.now(), JSONObject(patchJson))
        override fun spanClose(id: String, endIso: String) = spans.close(UUID.fromString(id), if (endIso.isEmpty()) db.now() else Instant.parse(endIso))
        override fun spanPut(output: String, rowJson: String, key: String): String {
            val o = output(output); val (cols, start, end) = parseRow(o, rowJson)
            return spans.put(m, o, start ?: throw IllegalStateException("$output: start가 없습니다"), end ?: throw IllegalStateException("$output: end가 없습니다"), cols, key.ifEmpty { null }).toString()
        }
        override fun spanDelete(output: String, id: String) = spans.delete(m, output(output), UUID.fromString(id))
        override fun emit(output: String, rowJson: String, key: String): String {
            val o = output(output); val (cols, time, _) = parseRow(o, rowJson)
            return spans.emit(m, o, time ?: throw IllegalStateException("$output: time이 없습니다"), cols, key.ifEmpty { null }).toString()
        }
        override fun stateGet(key: String): String = db.stateGet(m.id, cfg.device, key) ?: ""
        override fun stateSet(key: String, valueJson: String) { db.stateSet(m.id, cfg.device, key, valueJson) }
        override fun settings(): String = inst().settings.toString()
        override fun has(capability: String) = capability in inst().granted
        override fun now(): String = db.now().toString()
        override fun log(msg: String) = log(this@CollectorService, "[${m.id}] $msg")
    }

    private fun parseInterval(s: String): Long {
        val t = s.trim()
        if (t.endsWith("ms")) return t.dropLast(2).toDouble().toLong()
        val n = t.dropLast(1).toDouble()
        return when (t.last()) { 's' -> n * 1000; 'm' -> n * 60_000; 'h' -> n * 3_600_000; 'd' -> n * 86_400_000; else -> throw IllegalArgumentException("기간 형식이 아닙니다: $s") }.toLong()
    }
}
