package com.doorbell.app.ui

import android.os.Bundle
import android.view.View
import androidx.appcompat.app.AppCompatActivity
import com.doorbell.app.R
import com.doorbell.app.databinding.ActivityCallBinding
import com.doorbell.app.network.ServerRegistration
import com.google.gson.JsonObject

/**
 * Full-screen call activity — UI only.
 *
 * Audio is handled entirely by AudioStreamService (always-on mic → server,
 * and intercom playback from server). This activity just shows call state.
 */
class CallActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "CallActivity"
    }

    private lateinit var binding: ActivityCallBinding
    private var callActive = true
    private var callStartTime = 0L

    private val wsListener: (JsonObject) -> Unit = { msg -> handleServerMessage(msg) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        android.util.Log.i(TAG, "=== CallActivity onCreate ===")
        binding = ActivityCallBinding.inflate(layoutInflater)
        setContentView(binding.root)

        binding.tvCallStatus.text = "[ ringing ]"
        binding.btnEndCall.setOnClickListener { endCall() }

        ServerRegistration.addListener(wsListener)
    }

    private fun handleServerMessage(msg: JsonObject) {
        val type = msg.get("type")?.asString ?: return

        when (type) {
            "call_state" -> {
                val state = msg.get("state")?.asString ?: return
                android.util.Log.i(TAG, "Call state → $state")
                when (state) {
                    "ringing" -> runOnUiThread { binding.tvCallStatus.text = "[ ringing ]" }
                    "answered" -> onCallAnswered()
                    "idle", "ended" -> {
                        android.util.Log.i(TAG, "Call ended by server")
                        runOnUiThread { endCall() }
                    }
                }
            }
            "start_audio" -> {
                android.util.Log.i(TAG, "Server says: start audio (handled by AudioStreamService)")
            }
            "stop_audio" -> {
                android.util.Log.i(TAG, "Server says: stop audio (handled by AudioStreamService)")
            }
        }
    }

    private fun onCallAnswered() {
        callStartTime = System.currentTimeMillis()
        runOnUiThread {
            binding.tvCallStatus.text = "[ connected ]"
            binding.tvCallStatus.setTextColor(getColor(R.color.accent_green))
            binding.tvCallHint.text = "// homeowner speaking"
            binding.tvCallHint.visibility = View.VISIBLE
        }
        startCallTimer()
    }

    private fun startCallTimer() {
        Thread {
            while (callActive) {
                val elapsed = (System.currentTimeMillis() - callStartTime) / 1000
                val minutes = elapsed / 60
                val seconds = elapsed % 60
                runOnUiThread {
                    binding.tvCallTimer.text = String.format("%02d:%02d", minutes, seconds)
                    binding.tvCallTimer.visibility = View.VISIBLE
                }
                try { Thread.sleep(1000) } catch (_: InterruptedException) { break }
            }
        }.apply {
            isDaemon = true
            start()
        }
    }

    private fun endCall() {
        if (!callActive) return
        callActive = false
        ServerRegistration.endCall()
        finish()
    }

    override fun onDestroy() {
        super.onDestroy()
        callActive = false
        ServerRegistration.removeListener(wsListener)
    }
}
