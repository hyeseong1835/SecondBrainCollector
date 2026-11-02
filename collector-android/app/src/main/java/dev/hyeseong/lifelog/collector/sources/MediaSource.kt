package dev.hyeseong.lifelog.collector.sources

import android.content.ComponentName
import android.content.Context
import android.media.MediaMetadata
import android.media.session.MediaController
import android.media.session.MediaSession
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import org.json.JSONObject
import java.time.Instant

/**
 * 미디어 세션. 세션·메타데이터·재생 상태가 바뀔 때 { time, package, title, artist, album, duration_ms, playing }을 낸다.
 * 같은 값이 이어지면 내지 않는다. 세션이 사라지면 playing=false를 한 번 낸다. 설정 > 알림 접근 허용이 필요하다(리스너 컴포넌트는 NotificationSource.kt의 MediaListenerService).
 */
class MediaSource(private val context: Context) : EventSource {
    override val id = "media"
    private val msm = context.getSystemService(MediaSessionManager::class.java)
    private val component = ComponentName(context, MediaListenerService::class.java)
    private val handler = Handler(Looper.getMainLooper())
    private val hooked = HashMap<MediaSession.Token, Pair<MediaController, MediaController.Callback>>()
    private val last = HashMap<String, List<Any?>>()
    private var emit: ((JSONObject) -> Unit)? = null
    private val listener = MediaSessionManager.OnActiveSessionsChangedListener { hook(it ?: emptyList()) }

    companion object {
        fun hasAccess(context: Context): Boolean =
            (Settings.Secure.getString(context.contentResolver, "enabled_notification_listeners") ?: "").contains(context.packageName)
    }

    override fun start(emit: (JSONObject) -> Unit) {
        if (!hasAccess(context)) throw IllegalStateException("알림 접근 권한이 없습니다(설정 > 알림 접근)")
        this.emit = emit
        msm.addOnActiveSessionsChangedListener(listener, component, handler)
        handler.post { hook(msm.getActiveSessions(component)) }
    }

    private fun hook(list: List<MediaController>) {
        val tokens = list.map { it.sessionToken }.toSet()
        for (t in hooked.keys.toList()) if (t !in tokens) {
            val (c, cb) = hooked.remove(t)!!
            try { c.unregisterCallback(cb) } catch (_: Exception) {}
            report(c, gone = true)
        }
        for (c in list) if (c.sessionToken !in hooked) {
            val cb = object : MediaController.Callback() {
                override fun onPlaybackStateChanged(state: PlaybackState?) = report(c)
                override fun onMetadataChanged(metadata: MediaMetadata?) = report(c)
                override fun onSessionDestroyed() { hooked.remove(c.sessionToken); report(c, gone = true) }
            }
            c.registerCallback(cb, handler)
            hooked[c.sessionToken] = c to cb
            report(c)
        }
    }

    private fun report(c: MediaController, gone: Boolean = false) {
        val md = c.metadata
        val playing = !gone && c.playbackState?.state == PlaybackState.STATE_PLAYING
        val title = md?.getString(MediaMetadata.METADATA_KEY_TITLE) ?: ""
        val artist = md?.getString(MediaMetadata.METADATA_KEY_ARTIST) ?: ""
        val album = md?.getString(MediaMetadata.METADATA_KEY_ALBUM) ?: ""
        val duration = md?.getLong(MediaMetadata.METADATA_KEY_DURATION) ?: 0L
        val key = c.packageName
        val cur = listOf(title, artist, album, playing)
        if (last[key] == cur) return
        last[key] = cur
        if (title.isEmpty() && !playing) return
        emit?.invoke(JSONObject().put("time", Instant.now().toString()).put("package", key).put("title", title).put("artist", artist)
            .put("album", album).put("duration_ms", duration).put("playing", playing))
    }

    override fun close() {
        try { msm.removeOnActiveSessionsChangedListener(listener) } catch (_: Exception) {}
        for ((c, cb) in hooked.values) try { c.unregisterCallback(cb) } catch (_: Exception) {}
        hooked.clear()
    }
}
