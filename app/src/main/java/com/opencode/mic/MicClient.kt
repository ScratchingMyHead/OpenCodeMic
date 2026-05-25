package com.opencode.mic

import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

class MicClient {
    companion object {
        private const val TAG = "MicClient"
    }

    data class Config(
        val host: String = "192.168.1.100",
        val port: Int = 9876,
        val password: String = "",
        val useHttps: Boolean = false
    )

    suspend fun sendText(text: String, config: Config): Result<Unit> = withContext(Dispatchers.IO) {
        try {
            val scheme = if (config.useHttps) "https" else "http"
            val url = URL("$scheme://${config.host}:${config.port}/")
            Log.d(TAG, "POST to $url: $text")
            val conn = url.openConnection() as HttpURLConnection

            if (config.useHttps) {
                val trustAll = arrayOf<TrustManager>(object : X509TrustManager {
                    override fun checkClientTrusted(chain: Array<X509Certificate>?, authType: String?) {}
                    override fun checkServerTrusted(chain: Array<X509Certificate>?, authType: String?) {}
                    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
                })
                val sslContext = SSLContext.getInstance("TLS")
                sslContext.init(null, trustAll, SecureRandom())
                (conn as HttpsURLConnection).sslSocketFactory = sslContext.socketFactory
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
            }

            conn.apply {
                requestMethod = "POST"
                setRequestProperty("Content-Type", "application/json")
                if (config.password.isNotEmpty()) {
                    setRequestProperty("Authorization", "Bearer ${config.password}")
                }
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
