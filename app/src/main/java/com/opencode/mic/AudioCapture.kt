package com.opencode.mic

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.util.Log
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.isActive
import kotlin.coroutines.coroutineContext

class AudioCapture {
    companion object {
        const val SAMPLE_RATE = 16000
        const val CHUNK_MS = 2000
        const val CHUNK_SIZE = SAMPLE_RATE * CHUNK_MS / 1000
        private const val TAG = "AudioCapture"
    }

    fun record(): Flow<ShortArray> = flow {
        val bufferSize = AudioRecord.getMinBufferSize(
            SAMPLE_RATE,
            AudioFormat.CHANNEL_IN_MONO,
            AudioFormat.ENCODING_PCM_16BIT
        ).coerceAtLeast(CHUNK_SIZE * 2)

        Log.d(TAG, "Buffer size: $bufferSize, chunk size: $CHUNK_SIZE")

        val recorder = try {
            AudioRecord(
                MediaRecorder.AudioSource.MIC,
                SAMPLE_RATE,
                AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT,
                bufferSize
            )
        } catch (e: Exception) {
            Log.e(TAG, "Failed to create AudioRecord", e)
            throw e
        }

        if (recorder.state != AudioRecord.STATE_INITIALIZED) {
            Log.e(TAG, "AudioRecord not initialized (state=${recorder.state})")
            recorder.release()
            throw RuntimeException("AudioRecord failed to initialize")
        }

        try {
            recorder.startRecording()
            Log.d(TAG, "Recording started")
            val chunk = ShortArray(CHUNK_SIZE)

            while (coroutineContext.isActive) {
                val read = recorder.read(chunk, 0, CHUNK_SIZE)
                if (read > 0) {
                    val exact = if (read < CHUNK_SIZE) chunk.copyOf(read) else chunk
                    emit(exact)
                }
            }
        } finally {
            try {
                if (recorder.recordingState == AudioRecord.RECORDSTATE_RECORDING) {
                    recorder.stop()
                }
                recorder.release()
                Log.d(TAG, "Recorder released")
            } catch (e: Exception) {
                Log.e(TAG, "Error releasing recorder", e)
            }
        }
    }.flowOn(Dispatchers.IO)
}
