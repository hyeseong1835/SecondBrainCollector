package dev.hyeseong.lifelog.collector.sources

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Handler
import android.os.HandlerThread
import android.os.ParcelUuid
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant
import java.util.UUID

/**
 * 노트북 링크(BLE GATT 클라이언트). 노트북 수집기(phone_link 소스)가 광고하는 SecondBrain 서비스를 찾아 붙고,
 *   2초마다 연결 RSSI를 읽어 10초마다 묶어 보낸다({ t: rssi, s: [[몇 ms 전, rssi], …] }),
 *   5분마다 시계를 잰다(ping → pong → { t: clock, offset_ms, rtt_ms }. offset = 폰 시계 − 노트북 시계).
 * 끊기면 서비스 UUID 필터 스캔(저전력)으로 다시 찾는다. 스캔은 10분마다 새로 건다(오래된 스캔은 시스템이 기회적 스캔으로 낮춘다).
 * 낸 이벤트:
 *   { kind: link, state: connected | disconnected, name }
 *   { kind: rssi, rssi }  읽을 때마다
 *   { kind: clock, offset_ms, rtt_ms }
 * BLUETOOTH_SCAN(위치 안 씀)·BLUETOOTH_CONNECT 권한. 노트북 특성은 암호화가 필요해 처음 한 번 LE 페어링 확인이 뜰 수 있다.
 */
class LaptopLinkSource(private val context: Context) : EventSource {
    override val id = "laptop_link"

    companion object {
        val SERVICE: UUID = UUID.fromString("5b1e0001-7c3a-4f8e-9a1d-2f6c8e4b9d10")
        val RX: UUID = UUID.fromString("5b1e0002-7c3a-4f8e-9a1d-2f6c8e4b9d10")   // 폰 → 노트북
        val TX: UUID = UUID.fromString("5b1e0003-7c3a-4f8e-9a1d-2f6c8e4b9d10")   // 노트북 → 폰(알림)
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
        const val RSSI_EVERY = 2_000L
        const val SEND_EVERY = 10_000L
        const val CLOCK_EVERY = 300_000L
        const val RESCAN_EVERY = 600_000L
    }

    private val bm = context.getSystemService(BluetoothManager::class.java)
    private val thread = HandlerThread("lifelog-laptop-link").apply { start() }
    private val h = Handler(thread.looper)
    private lateinit var emit: (JSONObject) -> Unit
    private var gatt: BluetoothGatt? = null
    private var rx: BluetoothGattCharacteristic? = null
    private var ready = false
    private var scanning = false
    private var closed = false
    private var backoff = 5_000L
    private var name: String? = null
    private val pending = ArrayList<Pair<Long, Int>>()   // (elapsedRealtime, rssi)
    private var pingId = 0
    private val ops = ArrayDeque<() -> Boolean>()          // GATT 작업은 한 번에 하나
    private var busy = false

    private inline fun guard(what: String, body: () -> Unit) {
        try { body() } catch (e: Exception) { android.util.Log.w("LifeLog", "laptop_link $what 실패: $e") }
    }

    override fun start(emit: (JSONObject) -> Unit) {
        for (p in listOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT))
            if (context.checkSelfPermission(p) != PackageManager.PERMISSION_GRANTED) throw IllegalStateException("근처 기기 권한이 없습니다($p)")
        bm?.adapter ?: throw IllegalStateException("블루투스가 없는 기기입니다")
        this.emit = emit
        h.post { scan() }
    }

    private fun ev(o: JSONObject) { o.put("time", Instant.now().toString()); emit(o) }

    // ---- 찾기 ----
    private val scanCb = object : ScanCallback() {
        override fun onScanResult(type: Int, r: ScanResult) { h.post { found(r.device) } }
        override fun onScanFailed(code: Int) { h.post { scanning = false; retry("스캔 실패 $code") } }
    }

    @SuppressLint("MissingPermission")
    private fun scan(): Unit = guard("scan") {
        if (closed || gatt != null) return@guard
        val sc = bm.adapter?.bluetoothLeScanner ?: return@guard retry("블루투스 꺼짐")
        if (scanning) sc.stopScan(scanCb)
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(SERVICE)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_POWER).build()
        sc.startScan(listOf(filter), settings, scanCb)
        scanning = true
        h.removeCallbacks(rescan); h.postDelayed(rescan, RESCAN_EVERY)
    }
    private val rescan: Runnable = Runnable { scan() }

    @SuppressLint("MissingPermission")
    private fun stopScan(): Unit = guard("stopScan") {
        h.removeCallbacks(rescan)
        if (scanning) bm.adapter?.bluetoothLeScanner?.stopScan(scanCb)
        scanning = false
    }

    private fun retry(why: String) {
        if (closed) return
        android.util.Log.i("LifeLog", "laptop_link: $why, ${backoff / 1000}초 뒤 다시 찾기")
        h.removeCallbacks(rescan)
        h.postDelayed(rescan, backoff)
        backoff = (backoff * 2).coerceAtMost(60_000L)
    }

    @SuppressLint("MissingPermission")
    private fun found(d: BluetoothDevice): Unit = guard("connect") {
        if (closed || gatt != null) return@guard
        stopScan()
        name = d.name
        gatt = d.connectGatt(context, false, cb, BluetoothDevice.TRANSPORT_LE, BluetoothDevice.PHY_LE_1M_MASK, h)
    }

    // ---- 연결 ----
    private val cb = object : BluetoothGattCallback() {
        @SuppressLint("MissingPermission")
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, state: Int) = guard("state") {
            if (state == BluetoothProfile.STATE_CONNECTED && status == BluetoothGatt.GATT_SUCCESS) {
                g.requestMtu(247)
            } else if (state == BluetoothProfile.STATE_DISCONNECTED || status != BluetoothGatt.GATT_SUCCESS) {
                drop(g, "끊김 status=$status")
            }
        }

        @SuppressLint("MissingPermission")
        override fun onMtuChanged(g: BluetoothGatt, mtu: Int, status: Int) = guard("mtu") {
            g.requestConnectionPriority(BluetoothGatt.CONNECTION_PRIORITY_LOW_POWER)
            if (!g.discoverServices()) drop(g, "서비스 찾기 실패")
        }

        @SuppressLint("MissingPermission")
        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) = guard("services") {
            val svc = g.getService(SERVICE)
            val r = svc?.getCharacteristic(RX); val t = svc?.getCharacteristic(TX)
            if (status != BluetoothGatt.GATT_SUCCESS || r == null || t == null) return@guard drop(g, "SecondBrain 서비스가 없음")
            rx = r
            g.setCharacteristicNotification(t, true)
            val d = t.getDescriptor(CCCD) ?: return@guard drop(g, "CCCD 없음")
            enqueue { g.writeDescriptor(d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE) == BluetoothGatt.GATT_SUCCESS }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) = guard("cccd") {
            done()
            if (status != BluetoothGatt.GATT_SUCCESS) return@guard drop(g, "알림 구독 실패 status=$status")
            if (!ready) {
                ready = true; backoff = 5_000L
                ev(JSONObject().put("kind", "link").put("state", "connected").put("name", name ?: JSONObject.NULL))
                h.postDelayed(readRssi, RSSI_EVERY); h.postDelayed(send, SEND_EVERY); h.postDelayed(ping, 3_000L)
            }
        }

        override fun onReadRemoteRssi(g: BluetoothGatt, rssi: Int, status: Int) = guard("rssi") {
            done()
            if (status == BluetoothGatt.GATT_SUCCESS && rssi < 0) {
                pending.add(SystemClock.elapsedRealtime() to rssi)
                ev(JSONObject().put("kind", "rssi").put("rssi", rssi))
            }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, c: BluetoothGattCharacteristic, status: Int) = guard("write") { done() }

        override fun onCharacteristicChanged(g: BluetoothGatt, c: BluetoothGattCharacteristic, value: ByteArray) = guard("notify") {
            val m = JSONObject(String(value, Charsets.UTF_8))
            if (m.optString("t") == "pong") {
                val c1 = System.currentTimeMillis(); val c0 = m.getLong("c0"); val s = m.getLong("s")
                val rtt = c1 - c0
                val offset = (c0 + c1) / 2 - s   // 폰 시계 − 노트북 시계
                send(JSONObject().put("t", "clock").put("offset_ms", offset).put("rtt_ms", rtt))
                ev(JSONObject().put("kind", "clock").put("offset_ms", offset).put("rtt_ms", rtt))
            }
        }
    }

    // ---- 주기 작업 ----
    @SuppressLint("MissingPermission")
    private val readRssi: Runnable = Runnable {
        val g = gatt
        if (ready && g != null) { enqueue { g.readRemoteRssi() }; h.postDelayed(readRssi, RSSI_EVERY) }
    }

    private val send: Runnable = Runnable {
        if (!ready) return@Runnable
        if (pending.isNotEmpty()) {
            val now = SystemClock.elapsedRealtime()
            val arr = JSONArray()
            for ((at, r) in pending) arr.put(JSONArray().put(now - at).put(r))
            pending.clear()
            send(JSONObject().put("t", "rssi").put("s", arr))
        }
        h.postDelayed(send, SEND_EVERY)
    }

    private val ping: Runnable = Runnable {
        if (!ready) return@Runnable
        send(JSONObject().put("t", "ping").put("id", ++pingId).put("c0", System.currentTimeMillis()))
        h.postDelayed(ping, CLOCK_EVERY)
    }

    @SuppressLint("MissingPermission")
    private fun send(o: JSONObject) {
        val g = gatt ?: return; val c = rx ?: return
        val bytes = o.toString().toByteArray(Charsets.UTF_8)
        // ping은 큐에서 기다리는 시간이 왕복에 들어가지 않도록 c0을 실제로 쓸 때 다시 매긴다
        enqueue {
            val b = if (o.optString("t") == "ping") o.put("c0", System.currentTimeMillis()).toString().toByteArray(Charsets.UTF_8) else bytes
            g.writeCharacteristic(c, b, BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT) == BluetoothGatt.GATT_SUCCESS
        }
    }

    // GATT 작업 큐: 앞 작업의 콜백(done)이 와야 다음을 건다. 걸기에 실패하면 버리고 다음으로
    private fun enqueue(op: () -> Boolean) { ops.addLast(op); next() }
    private fun done() { busy = false; h.removeCallbacks(opTimeout); next() }
    private val opTimeout = Runnable { busy = false; next() }
    private fun next() {
        while (!busy && ops.isNotEmpty()) {
            val op = ops.removeFirst()
            val ok = try { op() } catch (e: Exception) { false }
            if (ok) { busy = true; h.postDelayed(opTimeout, 5_000L) }
        }
    }

    @SuppressLint("MissingPermission")
    private fun drop(g: BluetoothGatt, why: String) {
        guard("close") { g.close() }
        if (gatt !== g) return
        gatt = null; rx = null
        ops.clear(); busy = false; pending.clear()
        h.removeCallbacks(readRssi); h.removeCallbacks(send); h.removeCallbacks(ping); h.removeCallbacks(opTimeout)
        if (ready) { ready = false; ev(JSONObject().put("kind", "link").put("state", "disconnected").put("name", name ?: JSONObject.NULL)) }
        retry(why)
    }

    @SuppressLint("MissingPermission")
    override fun close() {
        h.post {
            closed = true
            stopScan()
            gatt?.let { guard("disconnect") { it.disconnect() }; drop(it, "종료") }
            thread.quitSafely()
        }
    }
}
