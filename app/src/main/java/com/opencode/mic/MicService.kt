package com.opencode.mic

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.media.audiofx.NoiseSuppressor
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import java.io.File

class MicService : Service() {
    companion object {
        private const val TAG = "MicService"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "opencode_mic_foreground"
        private const val SAMPLE_RATE = 16000
        private const val SILENCE_READS_THRESHOLD = 16
        private const val DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
    }

    interface Listener {
        fun onTranscript(text: String)
        fun onDebug(msg: String)
    }

    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var pipelineJob: Job? = null
    private var heartBeatJob: Job? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var currentListener: Listener? = null
    private val pendingTranscripts = mutableListOf<String>()
    private var voskModel: VoskRecognizer? = null

    inner class LocalBinder : android.os.Binder() {
        fun getService(): MicService = this@MicService
    }

    private val binder = LocalBinder()

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onUnbind(intent: Intent?): Boolean {
        Log.d(TAG, "onUnbind")
        currentListener = null
        return true
    }

    override fun onRebind(intent: Intent?) {
        super.onRebind(intent)
        Log.d(TAG, "onRebind")
    }

    override fun onCreate() {
        super.onCreate()
        Log.d(TAG, "onCreate")
        createNotificationChannel()
    }

    fun setListener(listener: Listener?) {
        currentListener = listener
        if (listener != null && pendingTranscripts.isNotEmpty()) {
            for (t in pendingTranscripts) {
                listener.onTranscript(t)
            }
            pendingTranscripts.clear()
        }
    }

    fun isListening(): Boolean = pipelineJob?.isActive == true

    fun startListening(hostConfig: MicClient.Config, energyThreshold: Int = 0, speechThreshold: Int = 0, noiseSuppressorEnabled: Boolean = true) {
        if (pipelineJob?.isActive == true) return
        pendingTranscripts.clear()
        Log.d(TAG, "startListening host=$hostConfig energyThreshold=$energyThreshold speechThreshold=$speechThreshold nsEnabled=$noiseSuppressorEnabled")

        if (voskModel == null) {
            val prefs = getSharedPreferences("opencode_mic", MODE_PRIVATE)
            val modelName = prefs.getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL
            val modelDir = File(filesDir, modelName)
            if (!modelDir.isDirectory) {
                debug("Model directory missing at ${modelDir.absolutePath}")
                return
            }
            voskModel = VoskRecognizer()
            try {
                if (!voskModel!!.init(modelDir.absolutePath)) {
                    debug("Vosk model init failed: $modelName")
                    voskModel = null
                    return
                }
            } catch (e: Exception) {
                Log.e(TAG, "init threw", e)
                voskModel = null
                return
            }
            debug("Vosk model loaded: $modelName")
        }
        voskModel?.reset()

        val pm = getSystemService(POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "OpenCodeMic:mic")
        wakeLock?.acquire()
        startForeground(NOTIFICATION_ID, buildNotification())
        pipelineJob = serviceScope.launch { runPipeline(voskModel!!, hostConfig, energyThreshold, speechThreshold, noiseSuppressorEnabled) }
        heartBeatJob = serviceScope.launch {
            while (isActive) {
                delay(5000)
                Log.d(TAG, "heartbeat: alive")
            }
        }
    }

    fun stopListening() {
        Log.d(TAG, "stopListening")
        pipelineJob?.cancel()
        heartBeatJob?.cancel()
        pipelineJob = null
        heartBeatJob = null
    }

    private suspend fun runPipeline(vosk: VoskRecognizer, hostConfig: MicClient.Config, energyThreshold: Int = 0, speechThreshold: Int = 0, noiseSuppressorEnabled: Boolean = true) = coroutineScope {
        val bufferSize = AudioRecord.getMinBufferSize(SAMPLE_RATE, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT).coerceAtLeast(SAMPLE_RATE * 2)
        val format = AudioFormat.Builder()
            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
            .setSampleRate(SAMPLE_RATE)
            .setChannelMask(AudioFormat.CHANNEL_IN_MONO)
            .build()
        val recorder = AudioRecord.Builder()
            .setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            .setAudioFormat(format)
            .setBufferSizeInBytes(bufferSize)
            .build()

        Log.d(TAG, "AudioRecord created: state=${recorder.state}")
        Log.d(TAG, "  format: sampleRate=$SAMPLE_RATE channels=MONO encoding=PCM_16BIT bufferSize=$bufferSize")

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            recorder.release()
            debug("AudioRecord init failed")
            cleanupPipeline()
            return@coroutineScope
        }

        recorder.startRecording()

        var noiseSuppressor: NoiseSuppressor? = null
        if (noiseSuppressorEnabled && Build.VERSION.SDK_INT >= Build.VERSION_CODES.JELLY_BEAN) {
            try {
                noiseSuppressor = NoiseSuppressor.create(recorder.audioSessionId)
                noiseSuppressor?.enabled = true
                Log.d(TAG, "NoiseSuppressor created: ${noiseSuppressor != null}")
            } catch (e: Exception) {
                Log.w(TAG, "NoiseSuppressor not available: ${e.message}")
            }
        } else {
            Log.d(TAG, "NoiseSuppressor disabled by user")
        }

        debug("Recording")

        try {
            val micClient = MicClient()

            launch(Dispatchers.IO) {
                val smallBuf = ShortArray(1024)
                var reads = 0
                var totalSamples = 0L
                var totalTime = 0L
                var silenceReads = 0
                var speechSinceLastFlush = false
                var skipFeed = 0
                var lastSentText = ""
                var samplesFed = 0L

                while (isActive) {
                    val before = System.currentTimeMillis()
                    val n = recorder.read(smallBuf, 0, 1024)
                    val after = System.currentTimeMillis()
                    reads++
                    if (n > 0) {
                        totalSamples += n
                        totalTime += (after - before)

                        var maxVal = 0
                        for (i in 0 until n) {
                            val v = kotlin.math.abs(smallBuf[i].toInt())
                            if (v > maxVal) maxVal = v
                        }
                        val threshold = if (speechThreshold > 0) 500 + speechThreshold * 95 else 500
                        val thisHasSpeech = maxVal > threshold
                        if (thisHasSpeech) {
                            silenceReads = 0
                            speechSinceLastFlush = true
                        } else {
                            silenceReads++
                        }

                        if (skipFeed > 0) {
                            skipFeed--
                        } else if (thisHasSpeech || speechSinceLastFlush) {
                            vosk.acceptWaveform(smallBuf)
                            samplesFed += n
                        }

                        val shouldFlush = silenceReads >= SILENCE_READS_THRESHOLD && speechSinceLastFlush

                        if (shouldFlush) {
                            val text = vosk.getFinalText()
                            if (text.isNotBlank() && text != lastSentText) {
                                if (text.split(Regex("\\s+")).size < 3) {
                                    Log.d(TAG, "DEBUG_SHORT: \"$text\" samplesFed=$samplesFed speechThreshold=$threshold energyThreshold=${"%.1f".format(energyThreshold / 10.0)}%")
                                }
                                lastSentText = text
                                pendingTranscripts.add(text)
                                currentListener?.onTranscript(text)
                                Log.d(TAG, "TX: $text")
                                micClient.sendText(text, hostConfig)
                            }
                            silenceReads = 0
                            speechSinceLastFlush = false
                            skipFeed = 16
                            samplesFed = 0
                        }
                        if (reads % 60 == 0) Log.d(TAG, "producer: reads=$reads samples=$totalSamples silence=$silenceReads speech=$speechSinceLastFlush max=$maxVal")
                    } else {
                        Log.d(TAG, "producer read=$n took=${after - before}ms")
                    }
                }
                Log.d(TAG, "producer done: reads=$reads samples=$totalSamples")
                recorder.stop()
                recorder.release()
                Log.d(TAG, "Recorder stopped")
            }.join()
        } finally {
            try { noiseSuppressor?.release() } catch (_: Exception) {}
            cleanupPipeline()
        }
    }

    private fun cleanupPipeline() {
        try {
            wakeLock?.let { if (it.isHeld) it.release() }; wakeLock = null
        } catch (e: Exception) { Log.e(TAG, "cleanup error", e) }
        debug("Stopped")
        pipelineJob = null
        heartBeatJob = null
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    private fun debug(msg: String) {
        Log.d(TAG, msg)
        currentListener?.onDebug(msg)
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("OpenCodeMic")
            .setContentText("Recording")
            .setSmallIcon(android.R.drawable.ic_menu_info_details)
            .setOngoing(true)
            .setSilent(true)
            .build()
    }

    private fun createNotificationChannel() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val channel = NotificationChannel(
                CHANNEL_ID, "Microphone", NotificationManager.IMPORTANCE_LOW
            )
            val nm = getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(channel)
        }
    }

    override fun onDestroy() {
        Log.d(TAG, "onDestroy")
        pipelineJob?.cancel()
        heartBeatJob?.cancel()
        voskModel?.release()
        voskModel = null
        try {
            wakeLock?.let { if (it.isHeld) it.release() }
        } catch (_: Exception) {}
        super.onDestroy()
    }
}
