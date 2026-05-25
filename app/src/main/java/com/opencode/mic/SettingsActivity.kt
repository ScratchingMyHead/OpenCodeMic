package com.opencode.mic

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.DocumentsContract
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.SeekBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.SwitchCompat
import androidx.documentfile.provider.DocumentFile
import java.io.File
import java.io.FileOutputStream

class SettingsActivity : AppCompatActivity() {
    companion object {
        const val DEFAULT_MODEL = "vosk-model-small-en-us-0.15"
    }

    private val pickTreeDir = registerForActivityResult(ActivityResultContracts.OpenDocumentTree()) { uri ->
        if (uri != null) importModelFromUri(uri)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)

        val hostInput: EditText = findViewById(R.id.hostInput)
        val portInput: EditText = findViewById(R.id.portInput)
        val passwordInput: EditText = findViewById(R.id.passwordInput)
        val saveBtn: Button = findViewById(R.id.saveBtn)
        val browseBtn: Button = findViewById(R.id.browseModelBtn)
        val energySeekBar: SeekBar = findViewById(R.id.energyThresholdSeekBar)
        val energyValue: TextView = findViewById(R.id.energyThresholdValue)
        val speechSeekBar: SeekBar = findViewById(R.id.speechThresholdSeekBar)
        val speechValue: TextView = findViewById(R.id.speechThresholdValue)
        val nsToggle: SwitchCompat = findViewById(R.id.noiseSuppressorToggle)
        val httpsToggle: SwitchCompat = findViewById(R.id.httpsToggle)
        val modelGroup: RadioGroup = findViewById(R.id.modelRadioGroup)

        val prefs = getSharedPreferences("opencode_mic", MODE_PRIVATE)
        hostInput.setText(prefs.getString("host", "192.168.1.100"))
        portInput.setText(prefs.getInt("port", 9876).toString())
        passwordInput.setText(prefs.getString("password", ""))
        httpsToggle.isChecked = prefs.getBoolean("use_https", false)

        val savedEnergy = prefs.getInt("energy_threshold", 0)
        energySeekBar.progress = savedEnergy.coerceAtMost(100)
        updateEnergyLabel(energyValue, savedEnergy)

        val savedSpeech = prefs.getInt("speech_threshold", 0)
        speechSeekBar.progress = savedSpeech.coerceAtMost(100)
        updateSpeechLabel(speechValue, savedSpeech)

        nsToggle.isChecked = prefs.getBoolean("noise_suppressor_enabled", true)
        val selectedModel = prefs.getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL

        refreshModelList(modelGroup, selectedModel)

        energySeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, value: Int, fromUser: Boolean) {
                updateEnergyLabel(energyValue, value)
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        speechSeekBar.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: SeekBar?, value: Int, fromUser: Boolean) {
                updateSpeechLabel(speechValue, value)
            }
            override fun onStartTrackingTouch(seekBar: SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: SeekBar?) {}
        })

        browseBtn.setOnClickListener {
            pickTreeDir.launch(null)
        }

        saveBtn.setOnClickListener {
            val checkedId = modelGroup.checkedRadioButtonId
            val checkedRadio = if (checkedId != -1) findViewById<RadioButton>(checkedId) else null
            val modelName = checkedRadio?.tag?.toString() ?: DEFAULT_MODEL
            prefs.edit().apply {
                putString("host", hostInput.text.toString().trim())
                putInt("port", portInput.text.toString().trim().toIntOrNull() ?: 9876)
                putString("password", passwordInput.text.toString())
                putBoolean("use_https", httpsToggle.isChecked)
                putInt("energy_threshold", energySeekBar.progress)
                putInt("speech_threshold", speechSeekBar.progress)
                putBoolean("noise_suppressor_enabled", nsToggle.isChecked)
                putString("model", modelName)
                apply()
            }
            finish()
        }
    }

    private fun updateEnergyLabel(v: TextView, value: Int) {
        v.text = "Energy Threshold: ${"%.1f".format(value / 10.0)}%"
    }

    private fun updateSpeechLabel(v: TextView, value: Int) {
        v.text = if (value == 0) "Auto (500)" else "${500 + value * 95}"
    }

    private fun refreshModelList(modelGroup: RadioGroup, selected: String) {
        modelGroup.removeAllViews()
        val filesDir = filesDir
        val models = filesDir.listFiles()?.filter {
            it.isDirectory && it.name.startsWith("vosk-model-")
        }?.map { it.name }?.sorted() ?: emptyList()

        if (models.isEmpty()) {
            val rb = RadioButton(this)
            rb.text = "No models found in app data"
            rb.isEnabled = false
            modelGroup.addView(rb)
            return
        }

        for (name in models) {
            val rb = RadioButton(this)
            rb.id = View.generateViewId()
            rb.text = name
            rb.tag = name
            if (name == selected) rb.isChecked = true
            modelGroup.addView(rb)
        }
    }

    private fun importModelFromUri(uri: Uri) {
        Toast.makeText(this, "Importing model...", Toast.LENGTH_SHORT).show()
        Thread {
            try {
                contentResolver.takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION)
                val rootDoc = DocumentFile.fromTreeUri(this, uri) ?: run {
                    runOnUiThread { Toast.makeText(this, "Could not read directory", Toast.LENGTH_LONG).show() }
                    return@Thread
                }

                val modelDir = findModelDir(rootDoc)
                if (modelDir == null) {
                    runOnUiThread { Toast.makeText(this, "No Vosk model found (need am/final.mdl)", Toast.LENGTH_LONG).show() }
                    return@Thread
                }

                val modelName = modelDir.name
                if (modelName == null || !modelName.startsWith("vosk-model-")) {
                    runOnUiThread { Toast.makeText(this, "Directory name must start with vosk-model-", Toast.LENGTH_LONG).show() }
                    return@Thread
                }

                val destDir = File(filesDir, modelName)
                if (destDir.exists()) {
                    runOnUiThread { Toast.makeText(this, "Model '$modelName' already imported", Toast.LENGTH_LONG).show() }
                    return@Thread
                }

                copyDocumentTree(modelDir, destDir)
                runOnUiThread {
                    Toast.makeText(this, "Imported: $modelName", Toast.LENGTH_LONG).show()
                    val modelGroup: RadioGroup = findViewById(R.id.modelRadioGroup)
                    val selected = getSharedPreferences("opencode_mic", MODE_PRIVATE).getString("model", DEFAULT_MODEL) ?: DEFAULT_MODEL
                    refreshModelList(modelGroup, selected)
                }
            } catch (e: Exception) {
                runOnUiThread { Toast.makeText(this, "Import failed: ${e.message}", Toast.LENGTH_LONG).show() }
            }
        }.start()
    }

    private fun findModelDir(doc: DocumentFile): DocumentFile? {
        if (doc.findFile("am")?.findFile("final.mdl") != null) return doc
        for (child in doc.listFiles()) {
            if (child.isDirectory) {
                val found = findModelDir(child)
                if (found != null) return found
            }
        }
        return null
    }

    private fun copyDocumentTree(src: DocumentFile, destDir: File) {
        destDir.mkdirs()
        for (child in src.listFiles()) {
            val destChild = File(destDir, child.name ?: continue)
            if (child.isDirectory) {
                copyDocumentTree(child, destChild)
            } else {
                contentResolver.openInputStream(child.uri)?.use { input ->
                    FileOutputStream(destChild).use { output ->
                        input.copyTo(output)
                    }
                }
            }
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}
