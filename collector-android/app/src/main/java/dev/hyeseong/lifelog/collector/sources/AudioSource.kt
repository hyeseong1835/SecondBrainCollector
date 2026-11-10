package dev.hyeseong.lifelog.collector.sources

import android.Manifest
import android.annotation.SuppressLint
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaFormat
import android.media.MediaMuxer
import android.media.MediaRecorder
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.UUID
import kotlin.math.log10
import kotlin.math.max
import kotlin.math.sqrt

/**
 * 소리 있는 구간 녹음(SecondBrain 녹음 1차, 2026-11-11). 마이크를 계속 듣다가 주변 소음보다 크게 들리는 구간만 파일로 남긴다.
 *
 * - 16kHz 모노 PCM을 50ms 프레임으로 읽어 dBFS를 잰다. 소음 바닥(floor)은 조용할 때 빨리 내려가고 시끄러울 때 천천히 올라간다(적응형).
 * - 바닥 + [START_DB]를 [START_FRAMES] 프레임 넘기면 시작, 바닥 + [KEEP_DB] 아래가 [STOP_FRAMES] 프레임 이어지면 끝. 시작 전 [PREROLL_FRAMES]를 앞에 붙인다.
 * - 파일은 AAC 32kbps(.m4a). 쓰는 동안 `.part`, 다 쓰면 이름을 바꾼다. 지우지 않는다(삭제는 사람이 직접).
 * - 위치: 앱 전용 외부 저장소 `files/recordings/yyyy-MM-dd/yyyyMMdd-HHmmss-xxxxxxxx.m4a`(adb pull로 가져간다).
 * - 파일 하나가 끝날 때마다 { start, end, file, bytes, sha256, codec, duration_ms, mean_db, peak_db, floor_db }를 낸다.
 *
 * 마이크 포그라운드 서비스 권한(RECORD_AUDIO)이 있어야 한다. Android 14+는 부팅 직후(앱을 열기 전) 마이크 서비스를 시작할 수 없어 소리가 0으로 들어온다.
 */
class AudioSource(private val context: Context) : EventSource {
    override val id = "audio"

    companion object {
        const val RATE = 16_000
        const val FRAME = RATE / 20            // 50ms
        const val START_DB = 10.0
        const val KEEP_DB = 6.0
        const val START_FRAMES = 4             // 200ms
        const val STOP_FRAMES = 100            // 5초
        const val PREROLL_FRAMES = 20          // 1초
        const val MAX_FRAMES = 20 * 60 * 30    // 30분이면 나눈다
        const val CODEC = "aac-lc 32kbps 16kHz mono"
        fun dir(context: Context) = File(context.getExternalFilesDir(null), "recordings")
    }

    @Volatile private var stop = false
    private var thread: Thread? = null

    override fun start(emit: (JSONObject) -> Unit) {
        if (context.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) throw IllegalStateException("마이크 권한이 없습니다")
        stop = false
        thread = Thread({ runCatching { loop(emit) }.onFailure { android.util.Log.w("SecondBrain", "audio 소스 멈춤: $it") } }, "audio-source").apply { isDaemon = true; start() }
    }

    override fun close() { stop = true; thread?.join(3000); thread = null }

    @SuppressLint("MissingPermission")
    private fun loop(emit: (JSONObject) -> Unit) {
        val min = AudioRecord.getMinBufferSize(RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(MediaRecorder.AudioSource.MIC, RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, max(min, FRAME * 2 * 8))
        val preroll = ArrayDeque<ShortArray>()
        var floor = -60.0
        var loud = 0; var quiet = 0
        var seg: Segment? = null
        rec.startRecording()
        try {
            while (!stop) {
                val buf = ShortArray(FRAME)
                var n = 0
                while (n < FRAME && !stop) { val r = rec.read(buf, n, FRAME - n); if (r <= 0) break; n += r }
                if (n < FRAME) continue
                val db = dbfs(buf)
                // 소음 바닥: 조용해지면 빨리 따라 내려가고, 시끄러운 동안은 아주 천천히 올라간다(말소리가 바닥을 끌어올리지 않게)
                floor = if (db < floor) floor + (db - floor) * 0.1 else floor + (db - floor) * 0.002
                val s = seg
                if (s == null) {
                    preroll.addLast(buf); if (preroll.size > PREROLL_FRAMES) preroll.removeFirst()
                    loud = if (db > floor + START_DB) loud + 1 else 0
                    if (loud >= START_FRAMES) {
                        val startAt = Instant.now().minusMillis(preroll.size * 50L)
                        seg = Segment(startAt, floor).also { ns -> for (f in preroll) ns.write(f, dbfs(f)) }
                        preroll.clear(); quiet = 0; loud = 0
                    }
                } else {
                    s.write(buf, db)
                    quiet = if (db < floor + KEEP_DB) quiet + 1 else 0
                    if (quiet >= STOP_FRAMES || s.frames >= MAX_FRAMES) {
                        emit(s.finish(Instant.now(), floor))
                        seg = null; quiet = 0
                    }
                }
            }
        } finally {
            seg?.let { runCatching { emit(it.finish(Instant.now(), floor)) } }
            runCatching { rec.stop() }; rec.release()
        }
    }

    private fun dbfs(buf: ShortArray): Double {
        var sum = 0.0
        for (v in buf) sum += v.toDouble() * v
        val rms = sqrt(sum / buf.size) / 32768.0
        return if (rms <= 1e-9) -120.0 else 20 * log10(rms)
    }

    /** 파일 하나: AAC 인코더 → MP4 먹서. 쓰는 동안 `.part`. */
    private inner class Segment(val start: Instant, val floorAtStart: Double) {
        private val name = DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss").withZone(ZoneId.systemDefault()).format(start) + "-" + UUID.randomUUID().toString().take(8)
        private val day = DateTimeFormatter.ofPattern("yyyy-MM-dd").withZone(ZoneId.systemDefault()).format(start)
        val rel = "$day/$name.m4a"
        private val file = File(dir(context), rel).also { it.parentFile!!.mkdirs() }
        private val part = File(file.path + ".part")
        private val codec = MediaCodec.createEncoderByType(MediaFormat.MIMETYPE_AUDIO_AAC)
        private val muxer = MediaMuxer(part.path, MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4)
        private var track = -1
        private var samples = 0L
        var frames = 0; private set
        private var sumDb = 0.0; private var peak = -120.0
        private val info = MediaCodec.BufferInfo()

        init {
            val fmt = MediaFormat.createAudioFormat(MediaFormat.MIMETYPE_AUDIO_AAC, RATE, 1).apply {
                setInteger(MediaFormat.KEY_AAC_PROFILE, MediaCodecInfo.CodecProfileLevel.AACObjectLC)
                setInteger(MediaFormat.KEY_BIT_RATE, 32_000)
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, FRAME * 2 * 4)
            }
            codec.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            codec.start()
        }

        fun write(pcm: ShortArray, db: Double) {
            frames++; sumDb += db; if (db > peak) peak = db
            queue(pcm, false)
            drain(false)
        }

        private fun queue(pcm: ShortArray?, eos: Boolean) {
            var idx = codec.dequeueInputBuffer(10_000)
            var tries = 0
            while (idx < 0 && tries++ < 50) { drain(false); idx = codec.dequeueInputBuffer(10_000) }   // 입력 칸이 없으면 출력을 비우고 다시
            if (idx < 0) return
            val inBuf = codec.getInputBuffer(idx)!!
            inBuf.clear()
            if (pcm != null) for (v in pcm) { inBuf.put((v.toInt() and 0xff).toByte()); inBuf.put(((v.toInt() shr 8) and 0xff).toByte()) }
            val pts = samples * 1_000_000L / RATE
            if (pcm != null) samples += pcm.size
            codec.queueInputBuffer(idx, 0, inBuf.position(), pts, if (eos) MediaCodec.BUFFER_FLAG_END_OF_STREAM else 0)
        }

        private fun drain(eos: Boolean) {
            var waits = 0
            while (true) {
                val o = codec.dequeueOutputBuffer(info, if (eos) 10_000 else 0)
                when {
                    o == MediaCodec.INFO_TRY_AGAIN_LATER -> { if (!eos || ++waits > 100) return }
                    o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> { track = muxer.addTrack(codec.outputFormat); muxer.start() }
                    o >= 0 -> {
                        val out = codec.getOutputBuffer(o)!!
                        if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) info.size = 0
                        if (info.size > 0 && track >= 0) { out.position(info.offset); out.limit(info.offset + info.size); muxer.writeSampleData(track, out, info) }
                        codec.releaseOutputBuffer(o, false)
                        if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) return
                    }
                }
            }
        }

        fun finish(end: Instant, floor: Double): JSONObject {
            queue(null, true)
            drain(true)
            runCatching { codec.stop() }; codec.release()
            runCatching { muxer.stop() }; muxer.release()
            part.renameTo(file)
            val md = MessageDigest.getInstance("SHA-256")
            file.inputStream().use { s -> val b = ByteArray(1 shl 16); while (true) { val r = s.read(b); if (r <= 0) break; md.update(b, 0, r) } }
            return JSONObject()
                .put("start", start.toString()).put("end", end.toString())
                .put("file", rel).put("bytes", file.length())
                .put("sha256", md.digest().joinToString("") { "%02x".format(it) })
                .put("codec", CODEC).put("duration_ms", samples * 1000 / RATE)
                .put("mean_db", if (frames > 0) sumDb / frames else -120.0).put("peak_db", peak)
                .put("floor_db", (floorAtStart + floor) / 2)
        }
    }
}
