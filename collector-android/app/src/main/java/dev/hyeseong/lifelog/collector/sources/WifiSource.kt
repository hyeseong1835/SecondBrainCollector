package dev.hyeseong.lifelog.collector.sources

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.LinkProperties
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.ScanResult
import android.net.wifi.WifiInfo
import org.json.JSONArray
import org.json.JSONObject
import java.time.Instant

/**
 * 와이파이 연결. ConnectivityManager 네트워크 콜백으로 와이파이 네트워크가 잡히고(connected) 바뀌고(changed) 끊기는(lost) 것을 낸다.
 * { time, kind, network, ssid, bssid, rssi, frequency_mhz, link_speed_mbps, standard, security, validated, metered, …, link: { interface, addresses, gateways, dns, … } }
 * changed는 SSID·BSSID·인터넷 확인·종량제가 바뀔 때만(신호 세기 변화마다 오는 콜백은 버린다). 등록하면 이미 잡힌 네트워크도 connected로 온다.
 * SSID·BSSID는 위치 권한이 있어야 읽힌다(백그라운드에서는 "항상 허용"). 없으면 null.
 */
class WifiSource(private val context: Context) : EventSource {
    override val id = "wifi"
    private val cm = context.getSystemService(ConnectivityManager::class.java)
    private var callback: ConnectivityManager.NetworkCallback? = null

    private class State(var caps: NetworkCapabilities? = null, var link: LinkProperties? = null, var signature: String? = null)

    override fun start(emit: (JSONObject) -> Unit) {
        if (context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED) throw IllegalStateException("위치 권한이 없습니다(와이파이 이름을 읽으려면 필요)")
        val states = HashMap<Network, State>()   // 콜백은 한 스레드(ConnectivityThread)에서만 온다
        val cb = object : ConnectivityManager.NetworkCallback(FLAG_INCLUDE_LOCATION_INFO) {
            override fun onCapabilitiesChanged(network: Network, caps: NetworkCapabilities) {
                val s = states.getOrPut(network) { State() }
                s.caps = caps
                val o = json(network, caps, s.link)
                val sig = listOf("ssid", "bssid", "validated", "metered").joinToString("|") { o.opt(it).toString() }
                if (sig == s.signature) return
                o.put("kind", if (s.signature == null) "connected" else "changed")
                s.signature = sig
                emit(o)
            }
            override fun onLinkPropertiesChanged(network: Network, lp: LinkProperties) { states.getOrPut(network) { State() }.link = lp }
            override fun onLost(network: Network) {
                val s = states.remove(network) ?: return
                if (s.signature == null) return
                emit(json(network, s.caps, s.link).put("kind", "lost"))
            }
        }
        callback = cb
        // 기본 요청은 인터넷·VPN 아님 같은 조건이 붙는다. 인터넷 없는 와이파이도 기록하려고 비우고 와이파이만 건다
        val req = NetworkRequest.Builder().clearCapabilities().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build()
        cm.registerNetworkCallback(req, cb)
    }

    override fun close() { callback?.let { try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {} }; callback = null }

    private fun json(network: Network, caps: NetworkCapabilities?, lp: LinkProperties?): JSONObject {
        val o = JSONObject()
        o.put("time", Instant.now().toString())
        o.put("network", network.toString())
        val w = caps?.transportInfo as? WifiInfo
        o.put("ssid", w?.ssid?.takeUnless { it == WifiManagerUnknownSsid }?.removeSurrounding("\"") ?: JSONObject.NULL)
        o.put("bssid", w?.bssid?.takeUnless { it == "02:00:00:00:00:00" } ?: JSONObject.NULL)
        if (w != null) {
            o.put("rssi", w.rssi)
            o.put("frequency_mhz", w.frequency)
            o.put("link_speed_mbps", w.linkSpeed)
            o.put("tx_link_speed_mbps", w.txLinkSpeedMbps)
            o.put("rx_link_speed_mbps", w.rxLinkSpeedMbps)
            o.put("max_tx_speed_mbps", w.maxSupportedTxLinkSpeedMbps)
            o.put("max_rx_speed_mbps", w.maxSupportedRxLinkSpeedMbps)
            o.put("standard", standardName(w.wifiStandard))
            o.put("security", securityName(w.currentSecurityType))
            o.put("hidden_ssid", w.hiddenSSID)
            o.put("passpoint_fqdn", w.passpointFqdn ?: JSONObject.NULL)
            o.put("passpoint_provider", w.passpointProviderFriendlyName ?: JSONObject.NULL)
        }
        if (caps != null) {
            o.put("validated", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED))
            o.put("captive_portal", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_CAPTIVE_PORTAL))
            o.put("internet", caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET))
            o.put("metered", !caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_METERED))
            o.put("down_kbps", caps.linkDownstreamBandwidthKbps)
            o.put("up_kbps", caps.linkUpstreamBandwidthKbps)
            o.put("signal_strength", caps.signalStrength)
        }
        if (lp != null) {
            o.put("link", JSONObject()
                .put("interface", lp.interfaceName ?: JSONObject.NULL)
                .put("addresses", JSONArray(lp.linkAddresses.map { it.toString() }))
                .put("gateways", JSONArray(lp.routes.filter { it.isDefaultRoute && it.gateway != null }.map { it.gateway!!.hostAddress }.distinct()))
                .put("dns", JSONArray(lp.dnsServers.map { it.hostAddress }))
                .put("domains", lp.domains ?: JSONObject.NULL)
                .put("private_dns", lp.privateDnsServerName ?: JSONObject.NULL)
                .put("private_dns_active", lp.isPrivateDnsActive)
                .put("mtu", lp.mtu)
                .put("proxy", lp.httpProxy?.toString() ?: JSONObject.NULL))
        }
        return o
    }

    private fun standardName(s: Int) = when (s) {
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

    private companion object { const val WifiManagerUnknownSsid = "<unknown ssid>" }   // WifiManager.UNKNOWN_SSID
}
