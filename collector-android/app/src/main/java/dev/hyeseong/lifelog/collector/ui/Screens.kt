package dev.hyeseong.lifelog.collector.ui

import android.app.AppOpsManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import dev.hyeseong.lifelog.collector.sources.HealthSource
import dev.hyeseong.lifelog.collector.sources.SamsungHealthSource
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

// ---------- 공통 ----------

@Composable
private fun Section(title: String, content: @Composable ColumnScope.() -> Unit) {
    Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium); content()
        }
    }
}

@Composable
private fun KeyValue(k: String, v: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(k, color = MaterialTheme.colorScheme.onSurfaceVariant); Text(v)
    }
}

@Composable
private fun Screen(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(12.dp), verticalArrangement = Arrangement.spacedBy(10.dp), content = content)
}

// ---------- 상태 ----------

@Composable
fun StatusScreen(model: AppModel) {
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { while (true) { model.refresh(); delay(5000) } }   // 화면이 떠 있는 동안 5초마다
    Screen {
        Text("SecondBrain Collector", style = MaterialTheme.typography.titleLarge)
        Section("수집") {
            KeyValue("상태", if (model.running) "실행 중 · ${model.status}" else "꺼짐")
            KeyValue("큐 대기", if (model.queue < 0) "-" else "${model.queue}건")
            KeyValue("기기", model.cfg.device.ifEmpty { "(미설정)" })
            KeyValue("DB", if (model.cfg.host.isEmpty()) "(미설정)" else "${model.cfg.host}:${model.cfg.port}")
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(onClick = { model.startService(); scope.launch { delay(2000); model.refresh() } }, enabled = !model.running && model.cfg.isComplete) { Text("수집 시작") }
                OutlinedButton(onClick = { model.stopService(); scope.launch { delay(2500); model.refresh() } }, enabled = model.running) { Text("중지") }
                OutlinedButton(onClick = { scope.launch { model.refresh() } }) { Text("새로고침") }
            }
            if (!model.cfg.isComplete) Text("연결 탭에서 API(또는 DB) 접속을 먼저 설정하세요.", color = MaterialTheme.colorScheme.error)
            Text("켜 두면 부팅 뒤에도 자동으로 시작합니다. 중지하면 자동 시작도 꺼집니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Section("플러그인") {
            if (model.plugins.isEmpty()) Text("없음") else model.plugins.forEach { r ->
                val inst = r.install
                KeyValue(r.manifest.name, if (inst == null) "미등록" else if (inst.enabled) "켜짐 ${inst.installedVersion}" else "꺼짐")
            }
        }
        Section("로그 (최근 ${model.logLines.size}줄)") {
            Text(model.logLines.joinToString("\n").ifEmpty { "비어 있음" }, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
        }
    }
}

// ---------- 플러그인 목록 ----------

@Composable
fun PluginsScreen(model: AppModel, onOpen: (String) -> Unit) {
    val scope = rememberCoroutineScope()
    var url by remember { mutableStateOf("") }
    Screen {
        Text("플러그인", style = MaterialTheme.typography.titleLarge)
        if (model.pluginError.isNotEmpty()) Text(model.pluginError, color = MaterialTheme.colorScheme.error)
        Card(Modifier.fillMaxWidth()) {
            Column {
                model.plugins.forEachIndexed { i, r ->
                    if (i > 0) HorizontalDivider()
                    Row(Modifier.fillMaxWidth().clickable { onOpen(r.manifest.id) }.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text(r.manifest.name, style = MaterialTheme.typography.titleSmall)
                            val inst = r.install
                            Text("${r.manifest.id} ${r.manifest.version}" + when {
                                inst == null -> " · 미등록"
                                inst.installedVersion != r.manifest.version -> " · 등록된 버전 ${inst.installedVersion}"
                                else -> ""
                            }, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                            val caps = r.manifest.capabilities
                            val missing = r.install?.let { inst -> caps.filter { it.second && it.first !in inst.granted } }.orEmpty()
                            if (missing.isNotEmpty()) Text("필수 권한 미허용: ${missing.joinToString { it.first }}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
                        }
                        Switch(checked = r.install?.enabled == true, enabled = model.cfg.isComplete && !model.busy,
                            onCheckedChange = { on -> scope.launch { model.run(if (on) "켜기" else "끄기") { model.setEnabled(r, on) }; model.restartIfRunning(); model.refresh() } })
                    }
                }
                if (model.plugins.isEmpty()) Text("플러그인 없음", Modifier.padding(12.dp))
            }
        }
        Text("항목을 누르면 설정과 권한을 편집합니다. 스위치는 바로 반영되고, 수집 중이면 서비스를 다시 시작합니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Section("등록·설치") {
            Button(onClick = { scope.launch { model.run("등록") { model.registerAll() }; model.restartIfRunning(); model.refresh() } }, enabled = model.cfg.isComplete && !model.busy) { Text("내장 플러그인 전부 등록") }
            Text("앱에 들어 있는 플러그인을 DB에 등록합니다. 처음이면 권한을 전부 허용하고 켭니다. 이미 있으면 설정·권한은 그대로 두고 버전만 올립니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            OutlinedTextField(url, { url = it }, label = { Text("GitHub URL (…/tree/브랜치/폴더)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Button(onClick = { val u = url.trim(); if (u.isNotEmpty()) scope.launch { model.run("설치") { model.installUrl(u) }; model.restartIfRunning(); model.refresh() } },
                enabled = model.cfg.isComplete && !model.busy && url.isNotBlank()) { Text("URL로 설치") }
        }
    }
}

// ---------- 플러그인 설정 ----------

@Composable
fun PluginDetailScreen(model: AppModel, row: AppModel.PluginRow, onBack: () -> Unit) {
    val scope = rememberCoroutineScope()
    val m = row.manifest; val inst = row.install
    var enabled by remember(row) { mutableStateOf(inst?.enabled ?: true) }
    val granted = remember(row) { mutableStateMapOf<String, Boolean>().apply { m.capabilities.forEach { (id, req) -> this[id] = inst?.granted?.contains(id) ?: true || req } } }
    // 설정값은 문자열로 편집하고 저장할 때 선언 타입에 맞춰 변환한다
    val values = remember(row) {
        mutableStateMapOf<String, String>().apply {
            m.settingDecls.forEach { d ->
                val key = d.getString("key"); val cur = inst?.settings?.opt(key) ?: d.opt("default")
                this[key] = when (cur) { null, JSONObject.NULL -> ""; is JSONArray -> (0 until cur.length()).joinToString(", ") { cur.get(it).toString() }; else -> cur.toString() }
            }
        }
    }
    fun toJson(): JSONObject {
        val o = JSONObject()
        for (d in m.settingDecls) {
            val key = d.getString("key"); val raw = values[key].orEmpty().trim()
            when (d.getString("type")) {
                "number" -> raw.toDoubleOrNull()?.let { o.put(key, if (it % 1.0 == 0.0) it.toLong() else it) }
                "boolean" -> o.put(key, raw == "true")
                "list" -> o.put(key, JSONArray(raw.split(',').map { it.trim() }.filter { it.isNotEmpty() }))
                else -> if (raw.isNotEmpty()) o.put(key, raw)
            }
        }
        return o
    }
    Screen {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = onBack) { Text("← 목록") }
            Text(m.name, style = MaterialTheme.typography.titleLarge)
        }
        Text("${m.id} ${m.version}" + (if (inst == null) " · 미등록(저장하면 등록됩니다)" else ""), color = MaterialTheme.colorScheme.onSurfaceVariant)
        Section("실행") {
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                Text("켜기"); Switch(enabled, { enabled = it })
            }
        }
        Section("권한 (capabilities)") {
            if (m.capabilities.isEmpty()) Text("없음")
            m.capabilities.forEach { (id, req) ->
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) { Text(id); Text(if (req) "필수" else "선택", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                    Checkbox(checked = granted[id] == true, onCheckedChange = { granted[id] = it }, enabled = !req)
                }
            }
        }
        Section("설정") {
            if (m.settingDecls.isEmpty()) Text("설정 없음")
            m.settingDecls.forEach { d ->
                val key = d.getString("key"); val label = d.optString("label", key); val type = d.getString("type")
                when (type) {
                    "boolean" -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                        Text(label); Switch(values[key] == "true", { values[key] = it.toString() })
                    }
                    "enum" -> {
                        Text(label)
                        val opts = d.optJSONArray("values")?.let { a -> (0 until a.length()).map { a.get(it).toString() } }.orEmpty()
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { opts.forEach { v -> FilterChip(selected = values[key] == v, onClick = { values[key] = v }, label = { Text(v) }) } }
                    }
                    else -> OutlinedTextField(values[key].orEmpty(), { values[key] = it }, label = { Text(label + if (type == "list") " (쉼표로 구분)" else "") },
                        modifier = Modifier.fillMaxWidth(), singleLine = type != "list",
                        keyboardOptions = if (type == "number") KeyboardOptions(keyboardType = KeyboardType.Number) else KeyboardOptions.Default,
                        supportingText = { Text("$type · 기본값 ${d.opt("default") ?: "없음"}") })
                }
            }
        }
        Section("출력") {
            m.outputs.forEach { o -> Text("${o.name} (${o.kind}) · ${o.columns.keys.joinToString(", ")}", style = MaterialTheme.typography.bodySmall) }
        }
        Button(onClick = {
            scope.launch {
                model.run("저장") { model.saveInstall(row, enabled, toJson(), granted.filterValues { it }.keys) }
                model.restartIfRunning(); model.refresh(); onBack()
            }
        }, enabled = model.cfg.isComplete && !model.busy, modifier = Modifier.fillMaxWidth()) { Text("저장") }
        Text("저장하면 DB의 설치 정보가 바뀌고, 수집 중이면 서비스를 다시 시작합니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ---------- 권한 ----------

private class Perm(val name: String, val why: String, val ok: Boolean?, val action: () -> Unit)

@Composable
fun PermissionsScreen(model: AppModel) {
    val ctx = model.activity
    val scope = rememberCoroutineScope()
    var tick by remember { mutableIntStateOf(0) }
    var hcGranted by remember { mutableStateOf<Int?>(null) }
    var shGranted by remember { mutableStateOf<Int?>(null) }
    var msg by remember { mutableStateOf("") }
    val perms = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { tick++ }
    val health = rememberLauncherForActivityResult(PermissionController.createRequestPermissionResultContract()) { tick++ }
    fun has(p: String) = ctx.checkSelfPermission(p) == PackageManager.PERMISSION_GRANTED
    LaunchedEffect(tick) {
        hcGranted = try { if (HealthSource.available(ctx)) HealthConnectClient.getOrCreate(ctx).permissionController.getGrantedPermissions().count { it in HealthSource.PERMISSIONS } else null } catch (_: Throwable) { null }
        shGranted = try { if (SamsungHealthSource.INCLUDED && SamsungHealthSource.available(ctx)) SamsungHealthSource.granted(ctx).size else null } catch (_: Throwable) { null }
    }
    // 화면에 돌아오면(설정 앱에서 돌아온 뒤) 다시 읽는다
    LaunchedEffect(Unit) { while (true) { delay(2000); tick++ } }
    val usageOk = remember(tick) {
        val ops = ctx.getSystemService(Context.APP_OPS_SERVICE) as AppOpsManager
        ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, android.os.Process.myUid(), ctx.packageName) == AppOpsManager.MODE_ALLOWED
    }
    val listenerOk = remember(tick) { Settings.Secure.getString(ctx.contentResolver, "enabled_notification_listeners")?.contains(ctx.packageName) == true }
    val batteryOk = remember(tick) { (ctx.getSystemService(Context.POWER_SERVICE) as PowerManager).isIgnoringBatteryOptimizations(ctx.packageName) }
    val list = listOf(
        Perm("알림", "수집 중 상태 알림", has(android.Manifest.permission.POST_NOTIFICATIONS)) { perms.launch(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS)) },
        Perm("위치", "phone-location · phone-network (와이파이 이름)", has(android.Manifest.permission.ACCESS_FINE_LOCATION)) { perms.launch(arrayOf(android.Manifest.permission.ACCESS_FINE_LOCATION, android.Manifest.permission.ACCESS_COARSE_LOCATION)) },
        Perm("위치 항상 허용", "화면이 꺼져도 위치·와이파이 이름 기록", has(android.Manifest.permission.ACCESS_BACKGROUND_LOCATION)) { perms.launch(arrayOf(android.Manifest.permission.ACCESS_BACKGROUND_LOCATION)) },
        Perm("근처 기기", "phone-bluetooth (블루투스 연결 기기) · phone-laptop-link (노트북 링크)", has(android.Manifest.permission.BLUETOOTH_CONNECT) && has(android.Manifest.permission.BLUETOOTH_SCAN)) { perms.launch(arrayOf(android.Manifest.permission.BLUETOOTH_CONNECT, android.Manifest.permission.BLUETOOTH_SCAN)) },
        Perm("마이크", "phone-audio (소리 있는 구간 녹음)", has(android.Manifest.permission.RECORD_AUDIO)) { perms.launch(arrayOf(android.Manifest.permission.RECORD_AUDIO)) },
        Perm("신체 활동", "포그라운드 health 종류", has(android.Manifest.permission.ACTIVITY_RECOGNITION)) { perms.launch(arrayOf(android.Manifest.permission.ACTIVITY_RECOGNITION)) },
        Perm("사용 정보 접근", "phone-app (앱 사용 구간)", usageOk) { ctx.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)) },
        Perm("알림 접근", "phone-media (재생 중인 미디어) · phone-notification (알림)", listenerOk) { ctx.startActivity(Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)) },
        Perm("Health Connect", "health · 허용 ${hcGranted ?: "-"}/${HealthSource.PERMISSIONS.size}", when { !HealthSource.available(ctx) -> null; else -> hcGranted == HealthSource.PERMISSIONS.size }) {
            if (HealthSource.available(ctx)) health.launch(HealthSource.PERMISSIONS) else msg = "Health Connect 없음"
        },
        Perm("삼성 헬스 SDK", "health의 read:shealth · 허용 ${shGranted ?: "-"}개 (개발자 모드 필요)", if (!SamsungHealthSource.INCLUDED || !SamsungHealthSource.available(ctx)) null else (shGranted ?: 0) > 0) {
            scope.launch { msg = try { "삼성 헬스 SDK: 허용 ${SamsungHealthSource.requestPermissions(ctx)}개" } catch (e: Throwable) { "삼성 헬스 SDK: ${e.message}" }; tick++ }
        },
        Perm("배터리 최적화 제외", "백그라운드에서 죽지 않게", batteryOk) {
            ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).setData(Uri.parse("package:${ctx.packageName}")))
        },
    )
    Screen {
        Text("권한", style = MaterialTheme.typography.titleLarge)
        Card(Modifier.fillMaxWidth()) {
            Column {
                list.forEachIndexed { i, p ->
                    if (i > 0) HorizontalDivider()
                    Row(Modifier.fillMaxWidth().clickable { p.action() }.padding(12.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(when (p.ok) { true -> "●"; false -> "○"; null -> "–" }, color = when (p.ok) { true -> MaterialTheme.colorScheme.primary; false -> MaterialTheme.colorScheme.error; null -> MaterialTheme.colorScheme.onSurfaceVariant }, modifier = Modifier.width(24.dp))
                        Column(Modifier.weight(1f)) { Text(p.name); Text(p.why, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                        Text(when (p.ok) { true -> "허용"; false -> "열기"; null -> "없음" }, style = MaterialTheme.typography.labelMedium)
                    }
                }
            }
        }
        if (msg.isNotEmpty()) Text(msg)
        Text("● 허용됨 · ○ 아직 · – 이 폰/빌드에 없음. 항목을 누르면 허용 창이나 설정 화면이 열립니다. 삼성 헬스 SDK는 삼성 헬스 앱 > 설정 > 삼성 헬스 정보 > 버전 10번 탭 > 개발자 모드(Developer Mode for Data Read)를 먼저 켜야 합니다.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

// ---------- 연결 ----------

@Composable
fun ConnectionScreen(model: AppModel) {
    val cfg = model.cfg; val scope = rememberCoroutineScope()
    // tools/db/phone-setup.ps1이 adb `am start --es ...`로 넘기는 값으로 칸을 미리 채운다(저장은 버튼을 눌러야)
    fun extra(k: String, def: String) = model.activity.intent?.getStringExtra(k)?.takeIf { it.isNotEmpty() } ?: def
    var device by remember { mutableStateOf(extra("device", cfg.device.ifEmpty { "phone" })) }
    var host by remember { mutableStateOf(extra("host", cfg.host)) }
    var port by remember { mutableStateOf(extra("port", cfg.port.toString())) }
    var database by remember { mutableStateOf(extra("database", cfg.database)) }
    var tenant by remember { mutableStateOf(extra("tenant", cfg.tenant)) }
    var ssl by remember { mutableStateOf(cfg.ssl) }
    var password by remember { mutableStateOf(extra("password", "")) }
    var apiUrl by remember { mutableStateOf(extra("api_url", cfg.apiUrl)) }
    var apiKey by remember { mutableStateOf(extra("api_key", "")) }
    Screen {
        Text("연결", style = MaterialTheme.typography.titleLarge)
        Section("SecondBrain API (권장)") {
            OutlinedTextField(device, { device = it }, label = { Text("기기 id (키 이름과 같게)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(apiUrl, { apiUrl = it }, label = { Text("API 주소 (비우면 아래 DB 직접 연결)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            OutlinedTextField(apiKey, { apiKey = it }, label = { Text(if (cfg.apiKey.isEmpty()) "API 키 (종류 collector)" else "API 키 (저장됨 · 바꿀 때만 입력)") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                cfg.device = device.trim(); cfg.apiUrl = apiUrl.trim()
                if (apiKey.isNotEmpty()) cfg.apiKey = apiKey.trim()
                apiKey = ""
                scope.launch { model.run("연결 확인") { model.testConnection() } }
            }, enabled = !model.busy) { Text("저장하고 연결 확인") }
            Text(if (cfg.usesApi) "지금: API 모드" else "지금: DB 직접 연결", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Section("DB 직접 연결 (옛 방식)") {
            OutlinedTextField(host, { host = it }, label = { Text("호스트") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(port, { port = it }, label = { Text("포트") }, singleLine = true, modifier = Modifier.weight(1f), keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number))
                OutlinedTextField(database, { database = it }, label = { Text("DB") }, singleLine = true, modifier = Modifier.weight(1f))
            }
            OutlinedTextField(tenant, { tenant = it }, label = { Text("테넌트 (Supabase 프로젝트 ref, 맨 Postgres면 비움)") }, singleLine = true, modifier = Modifier.fillMaxWidth())
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) { Text("SSL 필수"); Switch(ssl, { ssl = it }) }
            OutlinedTextField(password, { password = it }, label = { Text(if (cfg.password.isEmpty()) "기기 비밀번호" else "기기 비밀번호 (저장됨 · 바꿀 때만 입력)") }, singleLine = true,
                visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth())
            Button(onClick = {
                cfg.device = device.trim(); cfg.apiUrl = ""; apiUrl = ""; cfg.host = host.trim(); cfg.port = port.trim().toIntOrNull() ?: 5432; cfg.database = database.trim(); cfg.tenant = tenant.trim(); cfg.ssl = ssl
                if (password.isNotEmpty()) cfg.password = password
                password = ""
                scope.launch { model.run("연결 확인") { model.testConnection() } }
            }, enabled = !model.busy) { Text("저장하고 연결 확인") }
            Text("접속 사용자: ${cfg.user}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("비밀번호는 노트북에서 reset_device_password로 받아 붙여 넣습니다. Keystore로 암호화되어 이 폰에만 남습니다.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
