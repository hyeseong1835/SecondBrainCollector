package dev.hyeseong.lifelog.collector.core

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import java.io.File

/**
 * 수집기 설정. 기기 롤 비밀번호는 Keystore 기반 EncryptedSharedPreferences에만 둔다(Documents/인증·권한.md 3절).
 * 풀러(Supabase)는 사용자 이름이 `롤.프로젝트ref` 꼴이어야 한다 → tenant.
 */
class Config(context: Context) {
    private val prefs = EncryptedSharedPreferences.create(
        context, "lifelog-config", MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV, EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM)

    var device: String get() = prefs.getString("device", "") ?: ""; set(v) = prefs.edit().putString("device", v).apply()
    var host: String get() = prefs.getString("host", "") ?: ""; set(v) = prefs.edit().putString("host", v).apply()
    var port: Int get() = prefs.getInt("port", 5432); set(v) = prefs.edit().putInt("port", v).apply()
    var database: String get() = prefs.getString("database", "postgres") ?: "postgres"; set(v) = prefs.edit().putString("database", v).apply()
    var tenant: String get() = prefs.getString("tenant", "") ?: ""; set(v) = prefs.edit().putString("tenant", v).apply()
    var ssl: Boolean get() = prefs.getBoolean("ssl", true); set(v) = prefs.edit().putBoolean("ssl", v).apply()
    /** 사용자가 마지막으로 수집 시작을 눌러 둔 상태. 부팅 뒤 자동 시작 여부 */
    var autostart: Boolean get() = prefs.getBoolean("autostart", false); set(v) = prefs.edit().putBoolean("autostart", v).apply()
    /** 한 번만 하는 데이터 고침 표시(예: "steps_hourly_v1") */
    fun done(key: String) = prefs.getBoolean("done_$key", false)
    fun markDone(key: String) = prefs.edit().putBoolean("done_$key", true).apply()
    var password: String get() = prefs.getString("password", "") ?: ""; set(v) = prefs.edit().putString("password", v).apply()

    /** SecondBrainApi 주소(세컨뇌 DB 이전 7단계). 있으면 DB에 직접 붙지 않고 API를 거친다. 키는 종류 collector, 이름 = 기기 id. */
    var apiUrl: String get() = prefs.getString("api_url", "") ?: ""; set(v) = prefs.edit().putString("api_url", v).apply()
    var apiKey: String get() = prefs.getString("api_key", "") ?: ""; set(v) = prefs.edit().putString("api_key", v).apply()
    val usesApi get() = apiUrl.isNotBlank()

    val user get() = "lifelog_device_$device" + if (tenant.isEmpty()) "" else ".$tenant"
    val isComplete get() = device.isNotEmpty() && (if (usesApi) apiKey.isNotEmpty() else host.isNotEmpty() && password.isNotEmpty())

    fun jdbcUrl() = "jdbc:postgresql://$host:$port/$database?sslmode=${if (ssl) "require" else "prefer"}&ApplicationName=lifelog-collector-android&connectTimeout=15&socketTimeout=60"

    companion object {
        fun pluginsDir(context: Context) = File(context.filesDir, "plugins")
        fun logFile(context: Context) = File(context.filesDir, "collector.log")
        fun queuePath(context: Context) = File(context.filesDir, "queue.db").path
    }
}
