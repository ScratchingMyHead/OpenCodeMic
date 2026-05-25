package com.opencode.mic

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL

class MicClient {
    companion object {
        private const val TAG = "MicClient"
    }

    data class Config(
        val host: String = "192.168.1.100",
        val port: Int = 9876
    )

    suspend fun sendText(text: String, config: Config): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val url = URL("http://${config.host}:${config.port}/")
            Log.d(TAG, "POST to $url: $text")
            val conn = url.openConnection() as HttpURLConnection
            conn.apply {
                requestMethod = "POST"
                setRequestProperty("Content-Type", "application/json")
                doOutput = true
                connectTimeout = 3000
                readTimeout = 3000
            }

            val json = JSONObject().apply {
                put("text", text)
            }

            OutputStreamWriter(conn.outputStream).use { writer ->
                writer.write(json.toString())
                writer.flush()
            }

            val code = conn.responseCode
            conn.disconnect()

            if (code == 200) {
                Log.d(TAG, "POST OK")
                Result.success(Unit)
            } else {
                Log.w(TAG, "Server returned $code")
                Result.failure(Exception("Server returned $code"))
            }
        } catch (e: Exception) {
            Log.w(TAG, "HTTP failed: ${e.message}")
            Result.failure(e)
        }
    }
}
