package com.opencode.mic

import android.util.Log
import org.json.JSONObject
import org.vosk.Model
import org.vosk.Recognizer

class VoskRecognizer {
    companion object {
        private const val TAG = "Vosk"
    }

    private var model: Model? = null
    private var recognizer: Recognizer? = null

    fun init(modelPath: String): Boolean {
        try {
            model = Model(modelPath)
            recognizer = Recognizer(model, 16000.0f)
            Log.d(TAG, "Model loaded from $modelPath")
            return true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to load model", e)
            return false
        }
    }

    /** Feed audio without resetting internal state. */
    fun acceptWaveform(audioData: ShortArray): Boolean {
        val r = recognizer ?: return false
        return try {
            r.acceptWaveForm(audioData, audioData.size)
        } catch (e: Exception) {
            Log.e(TAG, "acceptWaveform error", e)
            false
        }
    }

    /** Get partial (in-progress) transcription — does not reset state. */
    fun getPartialText(): String {
        val r = recognizer ?: return ""
        return try {
            JSONObject(r.partialResult).optString("partial", "")
        } catch (e: Exception) {
            Log.e(TAG, "getPartialText error", e)
            ""
        }
    }

    /** Get final transcription for current utterance and reset. */
    fun getFinalText(): String {
        val r = recognizer ?: return ""
        return try {
            val result = r.result
            r.reset()
            val text = JSONObject(result).optString("text", "")
            if (text.isNotBlank()) Log.d(TAG, "final: $text")
            text
        } catch (e: Exception) {
            Log.e(TAG, "getFinalText error", e)
            ""
        }
    }

    /** Reset recognizer state without reloading the model. */
    fun reset() {
        try { recognizer?.reset() } catch (e: Exception) { Log.e(TAG, "reset error", e) }
    }

    /** Convenience: feed + finalize + reset (legacy, used during initial transient). */
    fun transcribe(audioData: ShortArray): String {
        acceptWaveform(audioData)
        return getFinalText()
    }

    fun release() {
        try {
            recognizer?.close()
            model?.close()
        } catch (e: Exception) {
            Log.e(TAG, "release error", e)
        }
        recognizer = null
        model = null
    }
}
