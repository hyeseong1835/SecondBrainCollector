package dev.hyeseong.lifelog.collector.dashboard

import android.annotation.SuppressLint
import android.net.Uri
import android.os.Bundle
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.updatePadding
import androidx.lifecycle.lifecycleScope
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebMessageCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import dev.hyeseong.lifelog.collector.core.Config
import dev.hyeseong.lifelog.collector.core.Db
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.ByteArrayInputStream
import java.io.File
import java.io.InputStream

/**
 * 휴대폰 대시보드. 컴퓨터 대시보드와 같은 셸(dashboard-windows/…/web)과 같은 렌더러 플러그인(plugins/widget-*)을 WebView로 띄운다.
 * - https://app.lifelog/… → 앱 assets의 셸 파일, sdk.js → assets/lifelog-sdk.js
 * - https://plugin-<id>.lifelog/… → filesDir/dashboard/plugins/<id>/…
 * 모든 HTML 응답의 <head> 맨 앞에 bridge.js를 넣는다(Windows의 AddScriptToExecuteOnDocumentCreated 대신).
 * 셸 ↔ 호스트는 웹 메시지 리스너 LifeLogHost(app.lifelog 출처의 메인 프레임에서만 보인다)로, 위젯 iframe은 셸을 거친다.
 * 개발용 인텐트 값: --ei offset N(N일 전), --es page settings|plugins
 */
class DashboardActivity : ComponentActivity() {
    private var bridge: DashboardBridge? = null
    private lateinit var web: WebView
    private val logFile by lazy { File(DashboardBridge.dir(this), "dashboard.log") }

    private fun log(s: String) {
        android.util.Log.i("LifeLogDash", s)
        try { logFile.appendText("${java.time.LocalDateTime.now().withNano(0)} $s\n") } catch (_: Exception) {}
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val cfg = Config(this)
        if (!cfg.isComplete) { setContentView(TextView(this).apply { text = "연결 설정이 없습니다. 세컨뇌 수집기 앱의 연결 탭에서 먼저 설정하세요."; setPadding(48, 96, 48, 48) }); return }
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) { setContentView(TextView(this).apply { text = "이 휴대폰의 WebView가 너무 오래됐습니다. Android System WebView를 업데이트하세요."; setPadding(48, 96, 48, 48) }); return }
        WebView.setWebContentsDebuggingEnabled(true)
        web = WebView(this)
        web.layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        web.setBackgroundColor(0xFF111111.toInt())
        web.settings.apply {
            javaScriptEnabled = true; domStorageEnabled = true
            allowFileAccess = false; allowContentAccess = false
            mediaPlaybackRequiresUserGesture = true
        }
        // 상태 표시줄·내비게이션 막대 아래로 내용이 들어가지 않게 바깥 틀에 여백을 준다(WebView 자체 padding은 내용에 안 먹는다)
        val root = android.widget.FrameLayout(this).apply { setBackgroundColor(0xFF111111.toInt()); addView(web) }
        setContentView(root)
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val b = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout())
            v.updatePadding(left = b.left, top = b.top, right = b.right, bottom = b.bottom); WindowInsetsCompat.CONSUMED
        }
        val b = DashboardBridge(this, cfg, ::log)
        bridge = b
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? = serve(request.url)
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean = request.url.host?.endsWith(".lifelog") != true   // 바깥 링크로 떠나지 않는다
        }
        WebViewCompat.addWebMessageListener(web, "LifeLogHost", setOf("https://app.lifelog")) { _, message: WebMessageCompat, _, isMainFrame, reply: JavaScriptReplyProxy ->
            if (!isMainFrame) return@addWebMessageListener
            val data = message.data ?: return@addWebMessageListener
            lifecycleScope.launch {
                val out = withContext(Dispatchers.IO) { b.handle(data) }
                reply.postMessage(out)
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                // 설정·플러그인 페이지면 보드로, 보드면 앱을 닫는다
                web.evaluateJavascript("(function(){var m=document.getElementById('main');if(m&&m.style.display==='none'){showPage('main');return 1}return 0})()") { r ->
                    if (r != "1") finish()
                }
            }
        })
        lifecycleScope.launch {
            val err = withContext(Dispatchers.IO) {
                try {
                    log("시작")
                    val ver = Db(cfg).use { it.schemaVersion() }
                    if (ver > Db.KNOWN_SCHEMA_VERSION) "DB 코어 스키마($ver)가 이 앱(${Db.KNOWN_SCHEMA_VERSION})보다 높습니다. 앱을 갱신하세요."
                    else { b.installBundled(); b.mapPlugins(); null }
                } catch (e: Throwable) { log("시작 실패: $e"); "DB에 연결하지 못했습니다: ${e.message}" }
            }
            if (err != null) { setContentView(TextView(this@DashboardActivity).apply { text = err; setPadding(48, 96, 48, 48) }); return@launch }
            val offset = intent.getIntExtra("offset", 0)
            val page = intent.getStringExtra("page")
            web.loadUrl("https://app.lifelog/index.html?offset=$offset" + (page?.let { "&page=$it" } ?: ""))
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        bridge?.let { b -> Thread { b.close() }.start() }
    }

    // ---- 가상 호스트 ----
    private val bridgeJs by lazy { assets.open("bridge.js").bufferedReader().readText() }

    private fun serve(url: Uri): WebResourceResponse? {
        val host = url.host ?: return null
        val path = (url.path ?: "/").trimStart('/').ifEmpty { "index.html" }
        return try {
            when {
                host == "app.lifelog" -> {
                    val name = if (path == "sdk.js") "lifelog-sdk.js" else path
                    if (name.contains("..") || name.contains('/')) return notFound()
                    respond(name, assets.open(name))
                }
                host.startsWith("plugin-") && host.endsWith(".lifelog") -> {
                    val id = host.removePrefix("plugin-").removeSuffix(".lifelog")
                    val f = bridge?.pluginFile(id, path) ?: return notFound()
                    respond(f.name, f.inputStream())
                }
                else -> null   // 지도 타일 등 바깥 요청은 WebView가 그대로
            }
        } catch (e: java.io.FileNotFoundException) { notFound() }
    }

    private fun notFound() = WebResourceResponse("text/plain", "utf-8", 404, "Not Found", emptyMap(), ByteArrayInputStream(ByteArray(0)))

    private fun respond(name: String, input: InputStream): WebResourceResponse {
        val ext = name.substringAfterLast('.', "").lowercase()
        val mime = when (ext) { "html", "htm" -> "text/html"; "js" -> "text/javascript"; "css" -> "text/css"; "json" -> "application/json"; "svg" -> "image/svg+xml"; "png" -> "image/png"; "jpg", "jpeg" -> "image/jpeg"; else -> "application/octet-stream" }
        var body: InputStream = input
        if (mime == "text/html") {
            // bridge.js를 문서 맨 앞에서 실행시킨다(페이지 스크립트보다 먼저 window.lifelog가 있어야 한다)
            val html = input.bufferedReader().use { it.readText() }
            val tag = "<script>$bridgeJs</script>"
            val i = Regex("<head[^>]*>", RegexOption.IGNORE_CASE).find(html)?.range?.last
            body = ByteArrayInputStream((if (i != null) html.substring(0, i + 1) + tag + html.substring(i + 1) else tag + html).toByteArray())
        }
        return WebResourceResponse(mime, if (mime.startsWith("text/") || mime == "application/json") "utf-8" else null, 200, "OK",
            mapOf("Access-Control-Allow-Origin" to "*", "Cache-Control" to "no-cache"), body)
    }
}
