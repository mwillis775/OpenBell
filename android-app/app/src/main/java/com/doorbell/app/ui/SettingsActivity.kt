package com.doorbell.app.ui

import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.doorbell.app.databinding.ActivitySettingsBinding

class SettingsActivity : AppCompatActivity() {

    private lateinit var binding: ActivitySettingsBinding

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivitySettingsBinding.inflate(layoutInflater)
        setContentView(binding.root)

        setSupportActionBar(binding.toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Settings"

        loadSettings()

        binding.btnSave.setOnClickListener {
            saveSettings()
        }
    }

    private fun loadSettings() {
        val prefs = getSharedPreferences("doorbell_prefs", MODE_PRIVATE)
        binding.etServerUrl.setText(prefs.getString("server_url", "http://192.168.0.181:5000"))
        binding.etDeviceName.setText(prefs.getString("device_name", "Front Door"))
        binding.etStreamPort.setText(prefs.getInt("stream_port", 8080).toString())
        binding.etCallTimeout.setText(prefs.getInt("call_timeout", 30).toString())
    }

    private fun saveSettings() {
        val prefs = getSharedPreferences("doorbell_prefs", MODE_PRIVATE)
        prefs.edit().apply {
            putString("server_url", binding.etServerUrl.text.toString().trim())
            putString("device_name", binding.etDeviceName.text.toString().trim())
            putInt("stream_port", binding.etStreamPort.text.toString().toIntOrNull() ?: 8080)
            putInt("call_timeout", binding.etCallTimeout.text.toString().toIntOrNull() ?: 30)
            apply()
        }
        Toast.makeText(this, "Settings saved", Toast.LENGTH_SHORT).show()
        finish()
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}
