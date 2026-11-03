package dev.hyeseong.lifelog.collector.sources

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.TelephonyNetworkSpecifier
import android.net.wifi.ScanResult
import android.net.wifi.WifiInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.HandlerThread
import android.telephony.SignalStrength
import android.telephony.SubscriptionManager
import android.telephony.TelephonyCallback
import android.telephony.TelephonyDisplayInfo
import android.telephony.TelephonyManager
import org.json.JSONArray
import org.json.JSONObject
import java.net.Inet4Address
import java.time.Instant
import java.util.concurrent.Executor

/**
 * 네트워크. ConnectivityManager 콜백(모든 네트워크 + 기본 네트워크)과 TelephonyCallback(모바일 신호·망 종류)을 한 스레드에서 받아 낸다.
 *   { kind: connected | changed | lost, transport, network, validated, metered, roaming, ip, gateway, link, (와이파이) ssid·bssid·frequency_mhz·standard·security·link_speed_mbps…, (모바일) carrier·sim_slot·rat }
 *     changed는 신호 세기를 뺀 값(SSID·BSSID·통신사·망 종류·로밍·인터넷 확인·종량제)이 바뀔 때만
 *   { kind: default, transport: wifi | cellular | vpn | ethernet | bluetooth | other | none, network, label }  실제 인터넷 경로가 바뀔 때. 시작할 때 한 번
 *   { kind: signal, transport: wifi | cellular, network, level 0~4, dbm }  세기가 바뀔 때마다(와이파이 RSSI, 모바일은 기본 데이터 SIM). 해석은 플러그인 몫
 * 와이파이 SSID·BSSID는 위치 권한이 있어야 읽힌다(백그라운드는 "항상 허용").
 */
class NetworkSource(private val context: Context) : EventSource {
    override val id = "network"
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private val wm = context.getSystemService(WifiManager::class.java)
    private val thread = HandlerThread("lifelog-network").apply { start() }
    private val handler = Handler(thread.looper)
    private val executor = Executor { handler.post(it) }
    private var callbacks = ArrayList<ConnectivityManager.NetworkCallback>()
    private var telephony: Pair<TelephonyManager, TelephonyCallback>? = null

    private class State(var caps: NetworkCapabilities? = null, var link: LinkProperties? = null, var signature: String? = null, var rssi: Int? = null)
    // 아래는 전부 handler 스레드에서만 만진다
    private val states = HashMap<Network, State>()
    private var rat: String? = null
    private var defaultSig: String? = null
    private lateinit var emit: (JSONObject) -> Unit

    /** 콜백 스레드에서 난 예외는 프로세스를 죽이므로 여기서 삼키고 로그만 남긴다. */
    private inline fun guard(what: String, body: () -> Unit) {
        try { body() } catch (e: Exception) { android.util.Log.w("LifeLog", "network 소스 $what 실패: $e") }
    }

    override fun start(emit: (JSONObject) -> Unit) {
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) throw IllegalStateException("위치 권한이 없습니다(와이파이 이름을 읽으려면 필요)")
        this.emit = emit
        val all = object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) = guard("caps") {
                val s = states.getOrPut(network) { State() }
                s.caps = caps
                connection(network, s)
                val w = caps.transportInfo as? WifiInfo
                if (w != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) && w.rssi != s.rssi && w.rssi > -127) {
                    s.rssi = w.rssi
                    emit(base("signal", "wifi", network).put("level", wifiLevel(w.rssi)).put("dbm", w.rssi))
                }
            }
            override fun onLinkPropertiesChanged(network: Network, lp: LinkProperties) = guard("link") {
                val s = states.getOrPut(network) { State() }
                s.link = lp
                if (s.signature != null) connection(network, s)   // IP가 늦게 붙으면 changed로 한 번 더(서명에 ip가 들어 있다)
            }
            override fun onLost(network: Network) = guard("lost") {
                val s = states.remove(network) ?: return@guard
                if (s.signature == null) return@guard
                emit(connectionJson("lost", network, s))
            }
        }
        // 기본 요청은 인터넷·VPN 아님 같은 조건이 붙는다. 비워서 모든 네트워크(VPN·인터넷 없는 와이파이 포함)를 받는다
        cm.registerNetworkCallback(NetworkRequest.Builder().clearCapabilities().build(), all, handler)
        callbacks.add(all)

        val def = object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) = guard("default") { defaultChanged(network, caps) }
            override fun onLost(network: Network) = guard("default") { defaultChanged(null, null) }
        }
        cm.registerDefaultNetworkCallback(def, handler)
        callbacks.add(def)
        handler.post { guard("default") { if (defaultSig == null && cm.activeNetwork == null) defaultChanged(null, null) } }

        startTelephony()
    }

    override fun close() {
        for (cb in callbacks) try { cm.unregisterNetworkCallback(cb) } catch (_: Exception) {}
        callbacks.clear()
        telephony?.let { (tm, cb) -> try { tm.unregisterTelephonyCallback(cb) } catch (_: Exception) {} }
        telephony = null
        thread.quitSafely()
    }

    /** 기본 데이터 SIM의 신호와 망 종류(LTE·5G NSA 같은 표시 종류). 권한이 모자라면 그 부분만 빠진다. */
    private fun startTelephony() {
        val base = context.getSystemService(TelephonyManager::class.java) ?: return
        val sub = SubscriptionManager.getDefaultDataSubscriptionId()
        val tm = if (sub != SubscriptionManager.INVALID_SUBSCRIPTION_ID) base.createForSubscriptionId(sub) else base
        val cb = object : TelephonyCallback(), TelephonyCallback.SignalStrengthsListener, TelephonyCallback.DisplayInfoListener {
            override fun onSignalStrengthsChanged(ss: SignalStrength) = guard("signal") {
                val dbm = ss.cellSignalStrengths.firstOrNull()?.dbm?.takeIf { it != Int.MAX_VALUE }
                // 신호는 인터넷용 모바일 연결에 붙인다(통화용 IMS 등도 같은 기지국이지만 연결 구간이 따로다)
                val cells = states.entries.filter { it.value.caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true && it.value.signature != null }
                val net = (cells.firstOrNull { it.value.caps!!.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) } ?: cells.firstOrNull())?.key
                emit(base("signal", "cellular", net).put("level", ss.level).put("dbm", dbm ?: JSONObject.NULL))
            }
            override fun onDisplayInfoChanged(info: TelephonyDisplayInfo) = guard("rat") {
                rat = ratName(info)
                for ((n, s) in states) if (s.caps?.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) == true) connection(n, s)
            }
        }
        try { tm.registerTelephonyCallback(executor, cb); telephony = tm to cb }
        catch (e: SecurityException) {
            // 망 종류(DisplayInfo)에 권한이 필요한 기기면 신호만이라도
            val sig = object : TelephonyCallback(), TelephonyCallback.SignalStrengthsListener { override fun onSignalStrengthsChanged(ss: SignalStrength) = cb.onSignalStrengthsChanged(ss) }
            try { tm.registerTelephonyCallback(executor, sig); telephony = tm to sig } catch (_: Exception) {}
        }
    }

    private fun connection(network: Network, s: State) {
        val o = connectionJson("", network, s)
        val sig = listOf("transport", "ssid", "bssid", "carrier", "rat", "roaming", "validated", "metered", "ip").joinToString("|") { "${o.opt(it)}" }   // 없는 키는 null
        if (sig == s.signature) return
        o.put("kind", if (s.signature == null) "connected" else "changed")
        s.signature = sig
        emit(o)
    }

    private fun defaultChanged(network: Network?, caps: NetworkCapabilities?) {
        val t = if (caps == null) "none" else transport(caps)
        val label: Any = when (t) {
            "wifi" -> ssid(caps!!.transportInfo as? WifiInfo) ?: JSONObject.NULL
            "cellular" -> carrier(caps!!) ?: JSONObject.NULL
            else -> JSONObject.NULL
        }
        val sig = "$t|$network|$label"
        if (sig == defaultSig) return
        defaultSig = sig
        emit(base("default", t, network).put("label", label))
    }

    private fun base(kind: String, transport: String, network: Network?) = JSONObject()
        .put("time", Instant.now().toString()).put("kind", kind).put("transport", transport).put("network", network?.toString() ?: JSONObject.NULL)

    private fun connectionJson(kind: String, network: Network, s: State): JSONObject {
        val caps = s.caps
        val o = base(kind, caps?.let { transport(it) } ?: "other", network)
        if (caps != null) {
            o.put("validated", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
            o.put("captive_portal", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL))
            o.put("internet", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET))
            o.put("metered", !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED))
            o.put("roaming", !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_ROAMING))
            o.put("down_kbps", caps.linkDownstreamBandwidthKbps)
            o.put("up_kbps", caps.linkUpstreamBandwidthKbps)
            val w = caps.transportInfo as? WifiInfo
            if (w != null) {
                o.put("ssid", ssid(w) ?: JSONObject.NULL)
                o.put("bssid", w.bssid?.takeUnless { it == "02:00:00:00:00:00" } ?: JSONObject.NULL)
                o.put("frequency_mhz", w.frequency)
                o.put("link_speed_mbps", w.linkSpeed)
                o.put("standard", standardName(w.wifiStandard))
                o.put("security", securityName(w.currentSecurityType))
            }
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) {
                o.put("carrier", carrier(caps) ?: JSONObject.NULL)
                o.put("sim_slot", subscription(caps)?.let { SubscriptionManager.getSlotIndex(it) }?.takeIf { it >= 0 } ?: JSONObject.NULL)
                o.put("rat", rat ?: JSONObject.NULL)
                o.put("purpose", purpose(caps))
            }
        }
        val lp = s.link
        if (lp != null) {
            // IPv4가 있으면 그것, 없으면(모바일은 IPv6만인 경우가 많다) 링크 로컬이 아닌 첫 주소
            val addrs = lp.linkAddresses.map { it.address }
            val ip = addrs.firstOrNull { it is Inet4Address } ?: addrs.firstOrNull { !it.isLinkLocalAddress }
            o.put("ip", ip?.hostAddress ?: JSONObject.NULL)
            o.put("gateway", lp.routes.firstOrNull { it.isDefaultRoute && it.gateway != null && !it.gateway!!.isAnyLocalAddress }?.gateway?.hostAddress ?: JSONObject.NULL)
            o.put("link", JSONObject()
                .put("interface", lp.interfaceName ?: JSONObject.NULL)
                .put("addresses", JSONArray(lp.linkAddresses.map { it.toString() }))
                .put("dns", JSONArray(lp.dnsServers.map { it.hostAddress }))
                .put("private_dns", lp.privateDnsServerName ?: JSONObject.NULL)
                .put("mtu", lp.mtu))
        }
        return o
    }

    private fun transport(c: NetworkCapabilities) = when {
        c.hasTransport(NetworkCapabilities.TRANSPORT_VPN) -> "vpn"
        c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
        c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
        c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
        c.hasTransport(NetworkCapabilities.TRANSPORT_BLUETOOTH) -> "bluetooth"
        else -> "other"
    }

    /** 모바일 네트워크의 용도. 인터넷이 있으면 internet, 아니면 OS가 붙인 용도 능력 중 첫째(통화 ims·문자 mms·위치 supl·긴급 emergency…). */
    private fun purpose(c: NetworkCapabilities): String {
        if (c.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)) return "internet"
        val named = listOf(
            NetworkCapabilities.NET_CAPABILITY_IMS to "ims", NetworkCapabilities.NET_CAPABILITY_MMS to "mms", NetworkCapabilities.NET_CAPABILITY_EIMS to "emergency",
            NetworkCapabilities.NET_CAPABILITY_SUPL to "supl", NetworkCapabilities.NET_CAPABILITY_XCAP to "xcap", NetworkCapabilities.NET_CAPABILITY_RCS to "rcs",
            NetworkCapabilities.NET_CAPABILITY_CBS to "cbs", NetworkCapabilities.NET_CAPABILITY_FOTA to "fota", NetworkCapabilities.NET_CAPABILITY_DUN to "dun",
            NetworkCapabilities.NET_CAPABILITY_IA to "ia", NetworkCapabilities.NET_CAPABILITY_MCX to "mcx", NetworkCapabilities.NET_CAPABILITY_ENTERPRISE to "enterprise")
        return named.firstOrNull { c.hasCapability(it.first) }?.second ?: "other"
    }

    private fun ssid(w: WifiInfo?) = w?.ssid?.takeUnless { it == "<unknown ssid>" }?.removeSurrounding("\"")   // WifiManager.UNKNOWN_SSID
    private fun subscription(c: NetworkCapabilities) = (c.networkSpecifier as? TelephonyNetworkSpecifier)?.subscriptionId
    private fun carrier(c: NetworkCapabilities): String? {
        val tm = context.getSystemService(TelephonyManager::class.java) ?: return null
        val sub = subscription(c)
        return (if (sub != null) tm.createForSubscriptionId(sub) else tm).networkOperatorName?.takeIf { it.isNotBlank() }
    }

    /** 와이파이 RSSI → 0~4 단계(OS가 상태바에 쓰는 기준). */
    private fun wifiLevel(rssi: Int): Int {
        val max = wm.maxSignalLevel.coerceAtLeast(1)
        return Math.round(wm.calculateSignalLevel(rssi) * 4f / max)
    }

    private fun ratName(i: TelephonyDisplayInfo): String? = when (i.overrideNetworkType) {
        TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_NSA -> "nr_nsa"
        TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_NR_ADVANCED -> "nr_adv"
        TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_ADVANCED_PRO -> "lte_adv"
        TelephonyDisplayInfo.OVERRIDE_NETWORK_TYPE_LTE_CA -> "lte_ca"
        else -> when (i.networkType) {
            TelephonyManager.NETWORK_TYPE_NR -> "nr"
            TelephonyManager.NETWORK_TYPE_LTE, TelephonyManager.NETWORK_TYPE_IWLAN -> "lte"
            TelephonyManager.NETWORK_TYPE_UMTS, TelephonyManager.NETWORK_TYPE_HSDPA, TelephonyManager.NETWORK_TYPE_HSUPA, TelephonyManager.NETWORK_TYPE_HSPA,
            TelephonyManager.NETWORK_TYPE_HSPAP, TelephonyManager.NETWORK_TYPE_TD_SCDMA, TelephonyManager.NETWORK_TYPE_EVDO_0, TelephonyManager.NETWORK_TYPE_EVDO_A,
            TelephonyManager.NETWORK_TYPE_EVDO_B, TelephonyManager.NETWORK_TYPE_EHRPD -> "3g"
            TelephonyManager.NETWORK_TYPE_GSM, TelephonyManager.NETWORK_TYPE_GPRS, TelephonyManager.NETWORK_TYPE_EDGE, TelephonyManager.NETWORK_TYPE_CDMA,
            TelephonyManager.NETWORK_TYPE_1xRTT, TelephonyManager.NETWORK_TYPE_IDEN -> "2g"
            TelephonyManager.NETWORK_TYPE_UNKNOWN -> null
            else -> "unknown_${i.networkType}"
        }
    }

    private fun standardName(s: Int): Any = when (s) {
        ScanResult.WIFI_STANDARD_LEGACY -> "legacy"; ScanResult.WIFI_STANDARD_11N -> "11n"; ScanResult.WIFI_STANDARD_11AC -> "11ac"
        ScanResult.WIFI_STANDARD_11AX -> "11ax"; ScanResult.WIFI_STANDARD_11AD -> "11ad"; ScanResult.WIFI_STANDARD_11BE -> "11be"
        ScanResult.WIFI_STANDARD_UNKNOWN -> JSONObject.NULL; else -> "unknown_$s"
    }

    /** WifiInfo.SECURITY_TYPE_* 이름. 상수 값은 플랫폼에 고정돼 있다. */
    private fun securityName(t: Int): Any = when (t) {
        0 -> "open"; 1 -> "wep"; 2 -> "psk"; 3 -> "eap"; 4 -> "sae"; 5 -> "eap_wpa3_enterprise_192"; 6 -> "owe"; 7 -> "wapi_psk"; 8 -> "wapi_cert"
        9 -> "eap_wpa3_enterprise"; 10 -> "osen"; 11 -> "passpoint_r1_r2"; 12 -> "passpoint_r3"; 13 -> "dpp"
        -1 -> JSONObject.NULL; else -> "unknown_$t"
    }
}
