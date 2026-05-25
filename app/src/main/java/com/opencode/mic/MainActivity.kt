package com.opencode.mic

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import android.widget.Button
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity(), MicService.Listener {
    companion object {
        private const val PERMISSION_REQUEST_RECORD_AUDIO = 100
        private const val TAG = "OpenCodeMic"
        private const val BUNDLED_MODEL = "vosk-model-small-en-us-0.15"
    }

    private var micService: MicService? = null
    private var bound = false
    private var isListening = false
    private var pendingStart = false

    private lateinit var toggleBtn: Button
    private lateinit var transcriptText: TextView
    private lateinit var debugText: TextView
    private lateinit var settingsBtn: Button

    private val hostConfig: MicClient.Config
        get() {
            val prefs = getSharedPreferences("opencode_mic", MODE_PRIVATE)
            return MicClient.Config(
                host = prefs.getString("host", "192.168.1.100") ?: "192.168.1.100",
                port = prefs.getInt("port", 9876),
                password = prefs.getString("password", "") ?: "",
                useHttps = prefs.getBoolean("use_https", false)
            )
        }

    private val energyThreshold: Int
        get() = getSharedPreferences("opencode_mic", MODE_PRIVATE).getInt("energy_threshold", 0)

    private val speechThreshold: Int
        get() = getSharedPreferences("opencode_mic", MODE_PRIVATE).getInt("speech_threshold", 0)

    private val noiseSuppressorEnabled: Boolean
        get() = getSharedPreferences("opencode_mic", MODE_PRIVATE).getBoolean("noise_suppressor_enabled", true)

    val selectedModel: String
        get() = getSharedPreferences("opencode_mic", MODE_PRIVATE).getString("model", BUNDLED_MODEL) ?: BUNDLED_MODEL

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as MicService.LocalBinder
            micService = binder.getService()
            micService?.setListener(this@MainActivity)
            bound = true
            if (pendingStart) {
                pendingStart = false
                micService?.startListening(hostConfig, energyThreshold, speechThreshold, noiseSuppressorEnabled)
            }
            if (micService?.isListening() == true) {
                isListening = true
                toggleBtn.text = "STOP"
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            micService = null
            bound = false
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        toggleBtn = findViewById(R.id.toggleBtn)
        transcriptText = findViewById(R.id.transcriptText)
        debugText = findViewById(R.id.logText)
        settingsBtn = findViewById(R.id.settingsBtn)

        toggleBtn.setOnClickListener {
            if (isListening) stopListening() else tryStartListening()
        }

        settingsBtn.setOnClickListener {
            startActivity(Intent(this, SettingsActivity::class.java))
        }

        copyBundledModelIfNeeded()
    }

    override fun onResume() {
        super.onResume()
        bindService(Intent(this, MicService::class.java), serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onPause() {
        super.onPause()
        if (bound) {
            micService?.setListener(null)
            unbindService(serviceConnection)
            bound = false
        }
    }

    // MicService.Listener
    override fun onTranscript(text: String) {
        runOnUiThread { transcriptText.append("$text ") }
    }

    override fun onDebug(msg: String) {
        debug(msg)
    }

    private fun debug(msg: String) {
        Log.d(TAG, msg)
        val line = "${SimpleDateFormat("HH:mm:ss.SSS", Locale.US).format(Date())} $msg\n"
        runOnUiThread { debugText.append(line) }
    }

    private fun tryStartListening() {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.RECORD_AUDIO), PERMISSION_REQUEST_RECORD_AUDIO)
            return
        }
        val config = hostConfig
        val intent = Intent(this, MicService::class.java)
        ContextCompat.startForegroundService(this, intent)
        if (micService != null) {
            micService!!.startListening(config, energyThreshold, speechThreshold, noiseSuppressorEnabled)
        } else {
            pendingStart = true
        }
        isListening = true
        toggleBtn.text = "STOP"
        transcriptText.text = ""
        debugText.text = "Ready\n"
        debug("Starting...")
    }

    private fun stopListening() {
        micService?.stopListening()
        isListening = false
        toggleBtn.text = "START"
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQUEST_RECORD_AUDIO && grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            val config = hostConfig
            val intent = Intent(this, MicService::class.java)
            ContextCompat.startForegroundService(this, intent)
            if (micService != null) {
                micService!!.startListening(config, energyThreshold, speechThreshold, noiseSuppressorEnabled)
            } else {
                pendingStart = true
            }
            isListening = true
            toggleBtn.text = "STOP"
            transcriptText.text = ""
        } else {
            Toast.makeText(this, "Microphone permission required", Toast.LENGTH_LONG).show()
        }
    }

    private fun copyBundledModelIfNeeded() {
        val modelDir = File(filesDir, BUNDLED_MODEL)
        val markerFile = File(modelDir, "am/final.mdl")
        if (markerFile.exists()) {
            debug("Bundled model exists (${modelDir.absolutePath})")
            return
        }
        if (modelDir.exists()) {
            modelDir.deleteRecursively()
        }
        debug("Copying bundled model from assets...")
        try {
            copyAssetDirectory(BUNDLED_MODEL, modelDir)
            debug("Bundled model copied to ${modelDir.absolutePath}")
        } catch (e: Exception) {
            runOnUiThread {
                debug("Bundled model copy failed: ${e.message}")
                Toast.makeText(this@MainActivity,
                    "Model not in assets/$BUNDLED_MODEL — download and place it there",
                    Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun isAssetDirectory(path: String): Boolean {
        return try {
            assets.open(path).use { false }
        } catch (e: java.io.FileNotFoundException) {
            true
        }
    }

    private fun copyAssetDirectory(assetPath: String, destDir: File) {
        val list = assets.list(assetPath)
        if (list == null || list.isEmpty()) {
            throw RuntimeException("Asset directory $assetPath is empty or does not exist")
        }
        destDir.mkdirs()
        for (name in list) {
            val src = "$assetPath/$name"
            val dst = File(destDir, name)
            if (isAssetDirectory(src)) {
                copyAssetDirectory(src, dst)
            } else {
                assets.open(src).use { input ->
                    dst.outputStream().use { output -> input.copyTo(output) }
                }
            }
        }
    }

    override fun onDestroy() {
        if (bound) {
            micService?.setListener(null)
            unbindService(serviceConnection)
        }
        super.onDestroy()
    }
}
