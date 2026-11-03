package dev.hyeseong.lifelog.collector.ui

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import dev.hyeseong.lifelog.collector.CollectorService
import dev.hyeseong.lifelog.collector.core.Config
import dev.hyeseong.lifelog.collector.core.Db
import dev.hyeseong.lifelog.collector.core.Manifest
import dev.hyeseong.lifelog.collector.core.PluginInstaller
import dev.hyeseong.lifelog.collector.core.Queue
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * 수집기 앱 화면. 아래 탭 4개: 상태 / 플러그인 / 권한 / 연결. 플러그인을 누르면 설정·권한 편집 화면이 따로 열린다.
 * 비밀번호는 노트북 대시보드/CLI에서 reset_device_password로 받아 연결 탭에 붙여 넣는다. 화면은 상태를 보여 주고 버튼을 누르면 Db를 직접 부른다.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        installBundledPlugins()
        setContent { MaterialTheme(colorScheme = darkColorScheme()) { App(this) } }
    }

    /** assets의 저장소 plugins/를 filesDir/plugins로 푼다(첫 실행·갱신). phone-·health만. */
    private fun installBundledPlugins() {
        val dest = Config.pluginsDir(this)
        for (id in assets.list("")!!.filter { it.startsWith("phone-") || it == "health" }) {
            val d = File(dest, id); d.mkdirs()
            for (f in assets.list(id)!!) assets.open("$id/$f").use { i -> File(d, f).outputStream().use { i.copyTo(it) } }
        }
    }
}

/** 화면이 공유하는 상태와 동작. 느린 일(DB)은 전부 IO에서. */
class AppModel(val activity: MainActivity) {
    val cfg = Config(activity)
    var running by mutableStateOf(CollectorService.running)
    var status by mutableStateOf(CollectorService.status)
    var queue by mutableStateOf(-1L)
    var plugins by mutableStateOf(listOf<PluginRow>())
    var pluginError by mutableStateOf("")
    var logLines by mutableStateOf(listOf<String>())
    var busy by mutableStateOf(false)
    var message by mutableStateOf("")

    class PluginRow(val manifest: Manifest, val dir: File, val install: Db.Install?)

    fun localPlugins(): List<Pair<Manifest, File>> = Config.pluginsDir(activity).listFiles().orEmpty()
        .filter { File(it, "manifest.json").exists() }.sortedBy { it.name }.map { Manifest.load(it) to it }

    suspend fun refresh() {
        running = CollectorService.running; status = CollectorService.status
        logLines = withContext(Dispatchers.IO) { Config.logFile(activity).takeIf { it.exists() }?.readLines()?.takeLast(60) ?: emptyList() }
        withContext(Dispatchers.IO) {
            queue = try { Queue(Config.queuePath(activity)).use { it.count() } } catch (_: Throwable) { -1L }
            val local = localPlugins()
            if (!cfg.isComplete) { plugins = local.map { (m, d) -> PluginRow(m, d, null) }; pluginError = "연결 설정이 없어 등록 상태를 모릅니다"; return@withContext }
            try {
                Db(cfg).use { db -> plugins = local.map { (m, d) -> PluginRow(m, d, db.getInstall(m.id, cfg.device)) } }
                pluginError = ""
            } catch (e: Throwable) { plugins = local.map { (m, d) -> PluginRow(m, d, null) }; pluginError = "등록 상태 조회 실패: ${e.message}" }
        }
    }

    /** 오래 걸리는 동작을 IO에서 돌리고 결과 문장을 메시지로. */
    suspend fun run(label: String, block: suspend () -> String) {
        busy = true; message = "$label…"
        message = try { withContext(Dispatchers.IO) { block() } } catch (e: Throwable) { "$label 실패: ${e.message}" }
        busy = false
        refresh()
    }

    fun startService() { cfg.autostart = true; activity.startForegroundService(Intent(activity, CollectorService::class.java)) }
    fun stopService() { cfg.autostart = false; activity.stopService(Intent(activity, CollectorService::class.java)) }

    /** 플러그인은 서비스가 시작할 때 읽으므로, 바뀌면 서비스를 다시 시작한다. */
    suspend fun restartIfRunning() {
        if (!CollectorService.running) return
        activity.stopService(Intent(activity, CollectorService::class.java))
        var waited = 0
        while (CollectorService.running && waited < 10000) { delay(250); waited += 250 }
        cfg.autostart = true; activity.startForegroundService(Intent(activity, CollectorService::class.java)); delay(1500)
    }

    suspend fun registerAll(): String = Db(cfg).use { db ->
        var n = 0
        for ((m, _) in localPlugins()) {
            db.registerPlugin(m, cfg.device)
            // 수집기 서비스가 먼저 등록하면 설치 행이 꺼짐·권한 없음으로 생긴다. 그것도 처음으로 본다
            val inst = db.getInstall(m.id, cfg.device)?.takeUnless { !it.enabled && it.granted.isEmpty() }
            // 이미 등록된 플러그인은 켜짐·설정·권한을 그대로 두고 버전만 올린다. 처음이면 권한 전부 허용하고 켠다
            val granted = inst?.granted?.toList() ?: m.capabilities.map { it.first }
            db.setInstall(m.id, cfg.device, m.version, inst?.enabled ?: true, inst?.settings?.toString(), JSONArray(granted).toString()); n++
        }
        "플러그인 ${n}개 등록"
    }

    suspend fun setEnabled(row: PluginRow, on: Boolean): String = Db(cfg).use { db ->
        val inst = row.install ?: run { db.registerPlugin(row.manifest, cfg.device); null }
        val granted = inst?.granted?.takeIf { it.isNotEmpty() }?.toList() ?: row.manifest.capabilities.map { it.first }
        db.setInstall(row.manifest.id, cfg.device, row.manifest.version, on, inst?.settings?.toString(), JSONArray(granted).toString())
        "${row.manifest.name} ${if (on) "켬" else "끔"}"
    }

    suspend fun saveInstall(row: PluginRow, enabled: Boolean, settings: JSONObject, granted: Set<String>): String = Db(cfg).use { db ->
        if (row.install == null) db.registerPlugin(row.manifest, cfg.device)
        db.setInstall(row.manifest.id, cfg.device, row.manifest.version, enabled, settings.toString(), JSONArray(granted.toList()).toString())
        "${row.manifest.name} 저장"
    }

    suspend fun installUrl(url: String): String = Db(cfg).use { db ->
        val r = PluginInstaller.install(db, cfg.device, Config.pluginsDir(activity), url, true, activity.cacheDir)
        "${r.manifest.id} ${r.manifest.version} 설치·켬"
    }

    suspend fun testConnection(): String = Db(cfg).use { "연결 확인: 스키마 버전 ${it.schemaVersion()}" }
}

enum class Tab(val label: String) { STATUS("상태"), PLUGINS("플러그인"), PERMISSIONS("권한"), CONNECTION("연결") }

@Composable
fun App(activity: MainActivity) {
    val model = remember { AppModel(activity) }
    var tab by remember { mutableStateOf(Tab.STATUS) }
    var detail by remember { mutableStateOf<String?>(null) }   // 플러그인 설정 화면에 열린 플러그인 id
    val snack = remember { SnackbarHostState() }
    LaunchedEffect(Unit) { model.refresh() }
    LaunchedEffect(model.message) { if (model.message.isNotEmpty()) snack.showSnackbar(model.message) }

    Scaffold(
        snackbarHost = { SnackbarHost(snack) },
        bottomBar = {
            if (detail == null) NavigationBar {
                Tab.entries.forEach { t ->
                    NavigationBarItem(selected = tab == t, onClick = { tab = t }, icon = { Text(t.label.take(1)) }, label = { Text(t.label) })
                }
            }
        }
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            val open = detail
            if (open != null) {
                val row = model.plugins.firstOrNull { it.manifest.id == open }
                if (row == null) detail = null else PluginDetailScreen(model, row, onBack = { detail = null })
            } else when (tab) {
                Tab.STATUS -> StatusScreen(model)
                Tab.PLUGINS -> PluginsScreen(model, onOpen = { detail = it })
                Tab.PERMISSIONS -> PermissionsScreen(model)
                Tab.CONNECTION -> ConnectionScreen(model)
            }
        }
    }
}
