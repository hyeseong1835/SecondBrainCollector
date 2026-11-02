package dev.hyeseong.lifelog.collector.sources

import android.app.Notification
import android.app.PendingIntent
import android.app.Person
import android.content.Context
import android.content.LocusId
import android.graphics.Bitmap
import android.graphics.drawable.Icon
import android.media.session.MediaSession
import android.os.Bundle
import android.os.Parcelable
import android.service.notification.NotificationListenerService
import android.service.notification.StatusBarNotification
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/**
 * 알림 리스너. 이름은 미디어 소스 때 지은 것이지만, 설정 > 알림 접근 허용 항목이 컴포넌트 이름을 기억하므로 바꾸지 않는다.
 * 미디어 세션 조회(MediaSource)와 알림 수집(NotificationSource) 둘 다 이 컴포넌트를 쓴다.
 * OS가 띄우고 유지하므로 수집기 서비스와 수명이 다르다. 수집기가 켜져 있을 때만(sink가 있을 때만) 이벤트를 넘긴다.
 */
class MediaListenerService : NotificationListenerService() {
    companion object {
        @Volatile var instance: MediaListenerService? = null; private set
        /** NotificationSource가 꽂는 출구. 없으면 알림 내용은 읽지 않는다. */
        @Volatile var sink: ((JSONObject) -> Unit)? = null
    }

    override fun onListenerConnected() {
        instance = this
        sink?.let { snapshot(it) }
    }

    override fun onListenerDisconnected() { instance = null }

    override fun onNotificationPosted(sbn: StatusBarNotification, rankingMap: RankingMap) {
        sink?.invoke(NotificationJson.event("posted", sbn, rankingMap, null))
    }

    override fun onNotificationRemoved(sbn: StatusBarNotification, rankingMap: RankingMap, reason: Int) {
        sink?.invoke(NotificationJson.event("removed", sbn, rankingMap, reason))
    }

    /** 지금 떠 있는 알림 전부를 snapshot으로 한 번 넘긴다(수집기 시작·리스너 연결 때). */
    fun snapshot(emit: (JSONObject) -> Unit) {
        val list = try { activeNotifications } catch (_: Exception) { null } ?: return
        val ranking = try { currentRanking } catch (_: Exception) { null }
        for (sbn in list) emit(NotificationJson.event("snapshot", sbn, ranking, null))
    }
}

/**
 * 알림 이벤트 소스. 알림이 뜨거나(posted) 사라지거나(removed, reason) 수집기가 켜질 때 떠 있는 것(snapshot)을
 * StatusBarNotification·Notification·extras·랭킹까지 얻을 수 있는 그대로 JSON으로 낸다. 해석·요약은 플러그인 몫.
 * 설정 > 알림 접근 허용이 필요하다(MediaSource와 같은 리스너).
 */
class NotificationSource(private val context: Context) : EventSource {
    override val id = "notification"

    override fun start(emit: (JSONObject) -> Unit) {
        if (!MediaSource.hasAccess(context)) throw IllegalStateException("알림 접근 권한이 없습니다(설정 > 알림 접근)")
        MediaListenerService.sink = emit
        MediaListenerService.instance?.snapshot(emit)
    }

    override fun close() { MediaListenerService.sink = null }
}

/** StatusBarNotification → JSON. 비트맵·PendingIntent처럼 저장할 수 없는 것은 모양만 남기고, 나머지는 값 그대로. */
object NotificationJson {
    private const val MAX_DEPTH = 8

    fun event(kind: String, sbn: StatusBarNotification, ranking: NotificationListenerService.RankingMap?, reason: Int?): JSONObject {
        val o = JSONObject()
        o.put("time", Instant.now().toString())
        o.put("kind", kind)
        o.put("key", sbn.key)
        o.put("package", sbn.packageName)
        o.put("id", sbn.id)
        o.put("tag", sbn.tag ?: JSONObject.NULL)
        o.put("post_time", Instant.ofEpochMilli(sbn.postTime).toString())
        o.put("user", sbn.user.toString())
        o.put("uid", sbn.uid)
        o.put("op_pkg", sbn.opPkg)
        o.put("group_key", sbn.groupKey ?: JSONObject.NULL)
        o.put("override_group_key", sbn.overrideGroupKey ?: JSONObject.NULL)
        o.put("ongoing", sbn.isOngoing)
        o.put("clearable", sbn.isClearable)
        o.put("is_group", sbn.isGroup)
        o.put("is_app_group", sbn.isAppGroup)
        if (reason != null) { o.put("reason", reasonName(reason)); o.put("reason_code", reason) }
        o.put("notification", notification(sbn.notification, 0))
        if (ranking != null) o.put("ranking", ranking(ranking, sbn.key))
        return o
    }

    fun notification(n: Notification, depth: Int): JSONObject {
        val o = JSONObject()
        o.put("when", if (n.`when` == 0L) JSONObject.NULL else Instant.ofEpochMilli(n.`when`).toString())
        o.put("flags", n.flags)
        o.put("flag_names", JSONArray(flagNames(n.flags)))
        o.put("category", n.category ?: JSONObject.NULL)
        o.put("channel_id", n.channelId ?: JSONObject.NULL)
        o.put("group", n.group ?: JSONObject.NULL)
        o.put("sort_key", n.sortKey ?: JSONObject.NULL)
        o.put("ticker", n.tickerText?.toString() ?: JSONObject.NULL)
        o.put("visibility", n.visibility)
        o.put("priority", n.priority)
        o.put("number", n.number)
        o.put("color", n.color)
        o.put("shortcut_id", n.shortcutId ?: JSONObject.NULL)
        o.put("locus_id", n.locusId?.id ?: JSONObject.NULL)
        o.put("timeout_after", n.timeoutAfter)
        o.put("badge_icon_type", n.badgeIconType)
        o.put("group_alert_behavior", n.groupAlertBehavior)
        o.put("settings_text", n.settingsText?.toString() ?: JSONObject.NULL)
        o.put("allow_system_generated_contextual_actions", n.allowSystemGeneratedContextualActions)
        o.put("has_content_intent", n.contentIntent != null)
        o.put("has_delete_intent", n.deleteIntent != null)
        o.put("has_full_screen_intent", n.fullScreenIntent != null)
        o.put("small_icon", n.smallIcon?.let { icon(it) } ?: JSONObject.NULL)
        o.put("large_icon", n.getLargeIcon()?.let { icon(it) } ?: JSONObject.NULL)
        o.put("bubble", n.bubbleMetadata?.let { b ->
            JSONObject().put("shortcut_id", b.shortcutId ?: JSONObject.NULL).put("auto_expand", b.autoExpandBubble).put("suppress_notification", b.isNotificationSuppressed)
                .put("desired_height", b.desiredHeight).put("desired_height_res_id", b.desiredHeightResId)
        } ?: JSONObject.NULL)
        o.put("actions", JSONArray().also { a -> n.actions?.forEach { a.put(action(it)) } })
        o.put("extras", bundle(n.extras, depth + 1))
        o.put("public_version", if (n.publicVersion != null && depth < 2) notification(n.publicVersion, depth + 1) else JSONObject.NULL)
        return o
    }

    private fun action(a: Notification.Action): JSONObject {
        val o = JSONObject()
        o.put("title", a.title?.toString() ?: JSONObject.NULL)
        o.put("semantic_action", a.semanticAction)
        o.put("is_contextual", a.isContextual)
        o.put("allow_generated_replies", a.allowGeneratedReplies)
        o.put("has_intent", a.actionIntent != null)
        o.put("icon", a.getIcon()?.let { icon(it) } ?: JSONObject.NULL)
        o.put("remote_inputs", JSONArray().also { arr ->
            a.remoteInputs?.forEach { r ->
                arr.put(JSONObject().put("result_key", r.resultKey).put("label", r.label?.toString() ?: JSONObject.NULL)
                    .put("allow_free_form", r.allowFreeFormInput).put("choices", JSONArray(r.choices?.map { it.toString() } ?: emptyList<String>()))
                    .put("edit_choices_before_sending", r.editChoicesBeforeSending))
            }
        })
        o.put("extras", bundle(a.extras, 2))
        return o
    }

    private fun icon(i: Icon): JSONObject {
        val o = JSONObject().put("type", iconType(i.type))
        if (i.type == Icon.TYPE_RESOURCE) { o.put("package", i.resPackage); o.put("res_id", i.resId) }
        if (i.type == Icon.TYPE_URI || i.type == Icon.TYPE_URI_ADAPTIVE_BITMAP) o.put("uri", i.uri.toString())
        return o
    }

    private fun iconType(t: Int) = when (t) {
        Icon.TYPE_BITMAP -> "bitmap"; Icon.TYPE_RESOURCE -> "resource"; Icon.TYPE_DATA -> "data"; Icon.TYPE_URI -> "uri"
        Icon.TYPE_ADAPTIVE_BITMAP -> "adaptive_bitmap"; Icon.TYPE_URI_ADAPTIVE_BITMAP -> "uri_adaptive_bitmap"; else -> "unknown:$t"
    }

    /** Bundle의 모든 키를 값 타입대로. 모르는 타입은 클래스 이름과 toString. */
    fun bundle(b: Bundle?, depth: Int): JSONObject {
        val o = JSONObject()
        if (b == null) return o
        for (k in b.keySet()) {
            @Suppress("DEPRECATION")
            val v = try { b.get(k) } catch (e: Exception) { "<읽기 실패: ${e.javaClass.simpleName}>" }
            o.put(k, value(v, depth))
        }
        return o
    }

    private fun value(v: Any?, depth: Int): Any = when {
        v == null -> JSONObject.NULL
        depth > MAX_DEPTH -> "<깊이 초과>"
        v is CharSequence -> v.toString()
        v is Boolean || v is Int || v is Long || v is Double || v is Float || v is Short || v is Byte -> v
        v is Char -> v.toString()
        v is Bundle -> bundle(v, depth + 1)
        v is Person -> JSONObject().put("name", v.name?.toString() ?: JSONObject.NULL).put("key", v.key ?: JSONObject.NULL)
            .put("uri", v.uri ?: JSONObject.NULL).put("bot", v.isBot).put("important", v.isImportant).put("icon", v.icon?.let { icon(it) } ?: JSONObject.NULL)
        v is Icon -> icon(v)
        v is Bitmap -> JSONObject().put("bitmap", "${v.width}x${v.height}")
        v is PendingIntent -> JSONObject().put("pending_intent", v.creatorPackage ?: JSONObject.NULL)
        v is MediaSession.Token -> JSONObject().put("media_session", true)
        v is Notification -> if (depth < 3) notification(v, depth + 1) else "<Notification>"
        v is LocusId -> v.id
        v is Array<*> -> JSONArray().also { a -> v.forEach { a.put(value(it, depth + 1)) } }
        v is IntArray -> JSONArray(v.toList())
        v is LongArray -> JSONArray(v.toList())
        v is DoubleArray -> JSONArray(v.toList())
        v is FloatArray -> JSONArray(v.toList())
        v is BooleanArray -> JSONArray(v.toList())
        v is ByteArray -> JSONObject().put("bytes", v.size)
        v is Collection<*> -> JSONArray().also { a -> v.forEach { a.put(value(it, depth + 1)) } }
        v is Map<*, *> -> JSONObject().also { m -> v.forEach { (k, x) -> m.put(k.toString(), value(x, depth + 1)) } }
        v is Parcelable -> JSONObject().put("class", v.javaClass.name).put("string", v.toString().take(2000))
        else -> JSONObject().put("class", v.javaClass.name).put("string", v.toString().take(2000))
    }

    private fun ranking(map: NotificationListenerService.RankingMap, key: String): Any {
        val r = NotificationListenerService.Ranking()
        if (!map.getRanking(key, r)) return JSONObject.NULL
        val o = JSONObject()
        o.put("rank", r.rank)
        o.put("importance", r.importance)
        o.put("importance_explanation", r.importanceExplanation?.toString() ?: JSONObject.NULL)
        o.put("is_ambient", r.isAmbient)
        o.put("matches_interruption_filter", r.matchesInterruptionFilter())
        o.put("suppressed_visual_effects", r.suppressedVisualEffects)
        o.put("override_group_key", r.overrideGroupKey ?: JSONObject.NULL)
        o.put("lockscreen_visibility_override", r.lockscreenVisibilityOverride)
        o.put("can_show_badge", r.canShowBadge())
        o.put("can_bubble", r.canBubble())
        o.put("user_sentiment", r.userSentiment)
        o.put("is_suspended", r.isSuspended)
        o.put("is_conversation", r.isConversation)
        o.put("conversation_shortcut_id", r.conversationShortcutInfo?.id ?: JSONObject.NULL)
        o.put("last_audibly_alerted", if (r.lastAudiblyAlertedMillis <= 0) JSONObject.NULL else Instant.ofEpochMilli(r.lastAudiblyAlertedMillis).toString())
        o.put("smart_replies", JSONArray(r.smartReplies?.map { it.toString() } ?: emptyList<String>()))
        o.put("smart_actions", JSONArray(r.smartActions?.map { it.title?.toString() ?: "" } ?: emptyList<String>()))
        o.put("channel", r.channel?.let { c ->
            JSONObject().put("id", c.id).put("name", c.name?.toString() ?: JSONObject.NULL).put("importance", c.importance).put("group", c.group ?: JSONObject.NULL)
                .put("description", c.description ?: JSONObject.NULL).put("conversation_id", c.conversationId ?: JSONObject.NULL)
                .put("sound", c.sound?.toString() ?: JSONObject.NULL).put("vibration", c.shouldVibrate()).put("lights", c.shouldShowLights())
                .put("bypass_dnd", c.canBypassDnd()).put("lockscreen_visibility", c.lockscreenVisibility).put("show_badge", c.canShowBadge())
        } ?: JSONObject.NULL)
        return o
    }

    private fun flagNames(f: Int): List<String> {
        val names = ArrayList<String>()
        fun add(bit: Int, name: String) { if (f and bit != 0) names.add(name) }
        add(Notification.FLAG_SHOW_LIGHTS, "show_lights"); add(Notification.FLAG_ONGOING_EVENT, "ongoing_event"); add(Notification.FLAG_INSISTENT, "insistent")
        add(Notification.FLAG_ONLY_ALERT_ONCE, "only_alert_once"); add(Notification.FLAG_AUTO_CANCEL, "auto_cancel"); add(Notification.FLAG_NO_CLEAR, "no_clear")
        add(Notification.FLAG_FOREGROUND_SERVICE, "foreground_service"); add(Notification.FLAG_LOCAL_ONLY, "local_only"); add(Notification.FLAG_GROUP_SUMMARY, "group_summary")
        add(Notification.FLAG_BUBBLE, "bubble"); add(0x00008000, "fsi_requested_but_denied")   // FLAG_FSI_REQUESTED_BUT_DENIED(공개 상수 아님)
        return names
    }

    /** NotificationListenerService.REASON_* 이름. 상수 값은 플랫폼에 고정돼 있다. */
    fun reasonName(r: Int) = when (r) {
        1 -> "click"; 2 -> "cancel"; 3 -> "cancel_all"; 4 -> "error"; 5 -> "package_changed"; 6 -> "user_stopped"; 7 -> "package_banned"
        8 -> "app_cancel"; 9 -> "app_cancel_all"; 10 -> "listener_cancel"; 11 -> "listener_cancel_all"; 12 -> "group_summary_canceled"
        13 -> "group_optimization"; 14 -> "package_suspended"; 15 -> "profile_turned_off"; 16 -> "unautobundled"; 17 -> "channel_banned"
        18 -> "snoozed"; 19 -> "timeout"; 20 -> "channel_removed"; 21 -> "clear_data"; 22 -> "assistant_cancel"; 23 -> "lockdown"
        else -> "unknown_$r"
    }
}
