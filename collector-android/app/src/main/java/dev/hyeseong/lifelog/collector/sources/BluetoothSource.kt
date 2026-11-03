package dev.hyeseong.lifelog.collector.sources

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothA2dp
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothHeadset
import android.bluetooth.BluetoothHearingAid
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Handler
import android.os.HandlerThread
import org.json.JSONObject
import java.time.Instant

/**
 * 블루투스. 시스템 브로드캐스트(어댑터 상태·ACL 링크·프로필 연결·A2DP 재생·배터리)를 한 스레드에서 받아 낸다.
 *   { kind: adapter, state: on | off | turning_on | turning_off }  시작할 때 한 번, 바뀔 때마다
 *   { kind: acl, state: connected | disconnected, transport: bredr | le | null, …기기 }  기기와의 링크
 *   { kind: profile, profile: a2dp | headset | hearing_aid | le_audio | hid | pan, state: connected | disconnected, …기기 }
 *   { kind: playing, playing: true | false, …기기 }  A2DP로 소리가 나가는 동안
 *   { kind: battery, level 0~100, …기기 }  기기가 알려 줄 때만(이어폰·워치 일부)
 *   기기 = address, name(별칭이 있으면 별칭), device_type(classic | le | dual), major_class(audio_video·phone…), device_class(BluetoothClass 정수)
 * 시작할 때 이미 연결된 기기는 프로필 프록시·GATT 목록으로 profile/acl connected를 한 번씩 낸다(snapshot: true). BLUETOOTH_CONNECT 권한이 필요하다.
 */
class BluetoothSource(private val context: Context) : EventSource {
    override val id = "bluetooth"
    private val bm = context.getSystemService(BluetoothManager::class.java)
    private val thread = HandlerThread("lifelog-bluetooth").apply { start() }
    private val handler = Handler(thread.looper)
    private var receiver: BroadcastReceiver? = null
    private val proxies = ArrayList<Pair<Int, BluetoothProfile>>()
    private lateinit var emit: (JSONObject) -> Unit

    private inline fun guard(what: String, body: () -> Unit) {
        try { body() } catch (e: Exception) { android.util.Log.w("LifeLog", "bluetooth 소스 $what 실패: $e") }
    }

    override fun start(emit: (JSONObject) -> Unit) {
        if (context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) throw IllegalStateException("근처 기기(블루투스 연결) 권한이 없습니다")
        val adapter = bm?.adapter ?: throw IllegalStateException("블루투스가 없는 기기입니다")
        this.emit = emit
        val r = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) = guard(i.action ?: "?") { received(i) }
        }
        val filter = IntentFilter().apply {
            addAction(BluetoothAdapter.ACTION_STATE_CHANGED)
            addAction(BluetoothDevice.ACTION_ACL_CONNECTED)
            addAction(BluetoothDevice.ACTION_ACL_DISCONNECTED)
            addAction(BluetoothA2dp.ACTION_PLAYING_STATE_CHANGED)
            addAction(ACTION_BATTERY)
            for (a in PROFILE_ACTIONS.keys) addAction(a)
        }
        context.registerReceiver(r, filter, null, handler, Context.RECEIVER_EXPORTED)
        receiver = r
        handler.post { guard("adapter") { emit(now("adapter").put("state", adapterState(adapter.state))) } }
        if (adapter.isEnabled) snapshot(adapter)
    }

    override fun close() {
        receiver?.let { try { context.unregisterReceiver(it) } catch (_: Exception) {} }
        receiver = null
        val adapter = bm?.adapter
        for ((p, proxy) in proxies) try { adapter?.closeProfileProxy(p, proxy) } catch (_: Exception) {}
        proxies.clear()
        thread.quitSafely()
    }

    @SuppressLint("MissingPermission")
    private fun received(i: Intent) {
        val action = i.action ?: return
        if (action == BluetoothAdapter.ACTION_STATE_CHANGED) {
            emit(now("adapter").put("state", adapterState(i.getIntExtra(BluetoothAdapter.EXTRA_STATE, -1))))
            return
        }
        @Suppress("DEPRECATION")   // 형식을 받는 판은 API 33부터
        val d = i.getParcelableExtra<BluetoothDevice>(BluetoothDevice.EXTRA_DEVICE) ?: return
        when (action) {
            BluetoothDevice.ACTION_ACL_CONNECTED, BluetoothDevice.ACTION_ACL_DISCONNECTED -> emit(device("acl", d)
                .put("state", if (action == BluetoothDevice.ACTION_ACL_CONNECTED) "connected" else "disconnected")
                .put("transport", when (i.getIntExtra(EXTRA_TRANSPORT, -1)) { 1 -> "bredr"; 2 -> "le"; else -> JSONObject.NULL }))
            BluetoothA2dp.ACTION_PLAYING_STATE_CHANGED -> emit(device("playing", d).put("playing", i.getIntExtra(BluetoothProfile.EXTRA_STATE, -1) == BluetoothA2dp.STATE_PLAYING))
            ACTION_BATTERY -> i.getIntExtra(EXTRA_BATTERY, -1).takeIf { it in 0..100 }?.let { emit(device("battery", d).put("level", it)) }
            else -> {
                // 연결 중·끊는 중은 건너뛴다
                val state = when (i.getIntExtra(BluetoothProfile.EXTRA_STATE, -1)) { BluetoothProfile.STATE_CONNECTED -> "connected"; BluetoothProfile.STATE_DISCONNECTED -> "disconnected"; else -> return }
                emit(device("profile", d).put("profile", PROFILE_ACTIONS[action]).put("state", state))
            }
        }
    }

    /** 이미 연결된 기기. 프로필 프록시는 비동기로 붙으므로 붙는 대로 낸다. */
    @SuppressLint("MissingPermission")
    private fun snapshot(adapter: BluetoothAdapter) {
        handler.post { guard("gatt") {
            for (d in bm.getConnectedDevices(BluetoothProfile.GATT)) emit(device("acl", d).put("state", "connected").put("transport", "le").put("snapshot", true))
        } }
        val listener = object : BluetoothProfile.ServiceListener {
            override fun onServiceConnected(profile: Int, proxy: BluetoothProfile) {
                handler.post { guard("snapshot") {
                    proxies.add(profile to proxy)
                    val name = PROFILES[profile] ?: return@guard
                    for (d in proxy.connectedDevices) emit(device("profile", d).put("profile", name).put("state", "connected").put("snapshot", true))
                } }
            }
            override fun onServiceDisconnected(profile: Int) {}
        }
        for (p in PROFILES.keys) try { adapter.getProfileProxy(context, listener, p) } catch (_: Exception) {}
    }

    private fun now(kind: String) = JSONObject().put("time", Instant.now().toString()).put("kind", kind)

    @SuppressLint("MissingPermission")
    private fun device(kind: String, d: BluetoothDevice): JSONObject {
        val cls = d.bluetoothClass
        return now(kind)
            .put("address", d.address)
            .put("name", (d.alias ?: d.name)?.takeIf { it.isNotBlank() } ?: JSONObject.NULL)
            .put("device_type", when (d.type) { BluetoothDevice.DEVICE_TYPE_CLASSIC -> "classic"; BluetoothDevice.DEVICE_TYPE_LE -> "le"; BluetoothDevice.DEVICE_TYPE_DUAL -> "dual"; else -> JSONObject.NULL })
            .put("major_class", cls?.let { MAJOR[it.majorDeviceClass] ?: "unknown_${it.majorDeviceClass}" } ?: JSONObject.NULL)
            .put("device_class", cls?.deviceClass ?: JSONObject.NULL)
    }

    private fun adapterState(s: Int) = when (s) {
        BluetoothAdapter.STATE_ON -> "on"; BluetoothAdapter.STATE_OFF -> "off"
        BluetoothAdapter.STATE_TURNING_ON -> "turning_on"; BluetoothAdapter.STATE_TURNING_OFF -> "turning_off"; else -> "unknown_$s"
    }

    private companion object {
        // 공개 API에 이름이 없거나 API 33부터인 것은 문자열·숫자 그대로(플랫폼에 고정된 값)
        const val ACTION_BATTERY = "android.bluetooth.device.action.BATTERY_LEVEL_CHANGED"
        const val EXTRA_BATTERY = "android.bluetooth.device.extra.BATTERY_LEVEL"
        const val EXTRA_TRANSPORT = "android.bluetooth.device.extra.TRANSPORT"
        val PROFILE_ACTIONS = mapOf(
            BluetoothA2dp.ACTION_CONNECTION_STATE_CHANGED to "a2dp",
            BluetoothHeadset.ACTION_CONNECTION_STATE_CHANGED to "headset",
            BluetoothHearingAid.ACTION_CONNECTION_STATE_CHANGED to "hearing_aid",
            "android.bluetooth.action.LE_AUDIO_CONNECTION_STATE_CHANGED" to "le_audio",
            "android.bluetooth.input.profile.action.CONNECTION_STATE_CHANGED" to "hid",
            "android.bluetooth.pan.profile.action.CONNECTION_STATE_CHANGED" to "pan")
        // BluetoothProfile 번호: HEADSET 1, A2DP 2, HID_HOST 4, PAN 5, HEARING_AID 21, LE_AUDIO 22
        val PROFILES = mapOf(1 to "headset", 2 to "a2dp", 4 to "hid", 5 to "pan", 21 to "hearing_aid", 22 to "le_audio")
        val MAJOR = mapOf(0x0000 to "misc", 0x0100 to "computer", 0x0200 to "phone", 0x0300 to "networking", 0x0400 to "audio_video", 0x0500 to "peripheral",
            0x0600 to "imaging", 0x0700 to "wearable", 0x0800 to "toy", 0x0900 to "health", 0x1F00 to "uncategorized")
    }
}
