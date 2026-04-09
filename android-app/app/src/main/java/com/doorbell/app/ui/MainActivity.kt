package com.doorbell.app.ui

import android.Manifest
import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.UserManager
import android.provider.Settings
import android.util.Log
import android.view.View
import android.view.WindowInsetsController
import android.view.WindowManager
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import com.doorbell.app.BuildConfig
import com.doorbell.app.R
import com.doorbell.app.databinding.ActivityMainBinding
import com.doorbell.app.network.ServerRegistration
import com.doorbell.app.receiver.DoorbellDeviceAdmin
import com.doorbell.app.service.AudioStreamService
import com.doorbell.app.service.CameraStreamService
import com.google.gson.JsonObject
import java.net.Inet4Address
import java.net.NetworkInterface
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private var streamPort = CameraStreamService.DEFAULT_PORT
    private val clockHandler = Handler(Looper.getMainLooper())
    private val timeFormat = SimpleDateFormat("HH:mm", Locale.getDefault())

    /** WebSocket listener for person detection — brightens screen when someone is at the door */
    private val personDetectionListener: (JsonObject) -> Unit = { msg ->
        val type = msg.get("type")?.asString
        when (type) {
            "person_detected" -> runOnUiThread { setScreenBrightness(1.0f) }
            "person_left"     -> runOnUiThread { setScreenBrightness(0.01f) }
        }
    }

    /** Indicator states */
    private enum class ConnectionState { DISCONNECTED, CONNECTING, CONNECTED }
    private var connState = ConnectionState.DISCONNECTED

    private val cameraPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) { permissions ->
        val cameraGranted = permissions[Manifest.permission.CAMERA] == true
        if (cameraGranted) {
            startCameraStream()
        } else {
            setIndicator(ConnectionState.DISCONNECTED)
            Toast.makeText(this, "Camera permission required", Toast.LENGTH_LONG).show()
        }
    }

    /** Clock tick — runs every second */
    private val clockRunnable = object : Runnable {
        override fun run() {
            binding.tvTime.text = timeFormat.format(Date())
            // Align next tick to the start of the next second
            val now = System.currentTimeMillis()
            val delay = 1000L - (now % 1000L)
            clockHandler.postDelayed(this, delay)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // Always keep screen on, but at minimum brightness to save power.
        // The screen must stay on for CameraX lifecycle, but we don't need
        // the display to be visible — it's a doorbell, not a tablet.
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        window.attributes = window.attributes.apply {
            screenBrightness = 0.01f   // lowest non-zero brightness
        }

        // Full immersive — hide status bar and navigation bar
        goImmersive()

        streamPort = getSharedPreferences("doorbell_prefs", MODE_PRIVATE)
            .getInt("stream_port", CameraStreamService.DEFAULT_PORT)

        // Close / exit button — leaves kiosk mode then exits
        binding.btnClose.setOnClickListener {
            stopLockTask()
            finishAffinity()
        }

        setupDoorbellButton()

        // Start live clock
        binding.tvTime.text = timeFormat.format(Date())
        clockHandler.post(clockRunnable)

        setIndicator(ConnectionState.DISCONNECTED)
        ServerRegistration.addListener(personDetectionListener)
        requestBatteryOptimizationExemption()
        enterKioskMode()
        requestCameraPermission()
    }

    override fun onResume() {
        super.onResume()
        goImmersive()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) goImmersive()
    }

    override fun onDestroy() {
        super.onDestroy()
        clockHandler.removeCallbacks(clockRunnable)
        ServerRegistration.removeListener(personDetectionListener)
    }

    /** Full-screen immersive sticky mode — hides status bar + nav bar */
    @Suppress("DEPRECATION")
    private fun goImmersive() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            window.insetsController?.let { ctrl ->
                ctrl.hide(android.view.WindowInsets.Type.statusBars() or
                          android.view.WindowInsets.Type.navigationBars())
                ctrl.systemBarsBehavior =
                    WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            }
        } else {
            window.decorView.systemUiVisibility = (
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                or View.SYSTEM_UI_FLAG_FULLSCREEN
                or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                or View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                or View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                or View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION)
        }
    }

    /** Set the indicator dot color: red / orange / green */
    private fun setIndicator(state: ConnectionState) {
        connState = state
        val color = when (state) {
            ConnectionState.DISCONNECTED -> ContextCompat.getColor(this, R.color.indicator_red)
            ConnectionState.CONNECTING   -> ContextCompat.getColor(this, R.color.indicator_orange)
            ConnectionState.CONNECTED    -> ContextCompat.getColor(this, R.color.indicator_green)
        }
        val drawable = binding.statusIndicator.background
        if (drawable is GradientDrawable) {
            drawable.setColor(color)
        } else {
            val gd = GradientDrawable()
            gd.shape = GradientDrawable.OVAL
            gd.setColor(color)
            binding.statusIndicator.background = gd
        }
    }

    private fun requestCameraPermission() {
        val perms = mutableListOf(Manifest.permission.CAMERA, Manifest.permission.RECORD_AUDIO)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            perms.add(Manifest.permission.POST_NOTIFICATIONS)
        }

        val needed = perms.filter {
            ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED
        }

        if (needed.isEmpty()) {
            startCameraStream()
        } else {
            cameraPermissionLauncher.launch(needed.toTypedArray())
        }
    }

    private fun startCameraStream() {
        val intent = Intent(this, CameraStreamService::class.java).apply {
            putExtra(CameraStreamService.EXTRA_PORT, streamPort)
        }
        ContextCompat.startForegroundService(this, intent)
        setIndicator(ConnectionState.CONNECTING)

        Handler(mainLooper).postDelayed({ registerWithServer() }, 1500)
    }

    private fun registerWithServer() {
        val serverUrl = getServerUrl()
        setIndicator(ConnectionState.CONNECTING)
        ServerRegistration.connect(serverUrl)
        Handler(mainLooper).postDelayed({
            if (ServerRegistration.isConnected()) {
                ServerRegistration.register(getLocalIpAddress(), streamPort)
                setIndicator(ConnectionState.CONNECTED)
                startAudioStream(serverUrl)
            } else {
                setIndicator(ConnectionState.CONNECTING)
                Handler(mainLooper).postDelayed({ registerWithServer() }, 2000)
            }
        }, 1000)
    }

    private fun startAudioStream(serverUrl: String) {
        val serverIp = serverUrl
            .removePrefix("http://").removePrefix("https://")
            .split(":").firstOrNull() ?: return

        val intent = Intent(this, AudioStreamService::class.java).apply {
            putExtra(AudioStreamService.EXTRA_SERVER_IP, serverIp)
        }
        ContextCompat.startForegroundService(this, intent)
    }

    private fun setupDoorbellButton() {
        binding.btnDoorbell.setOnClickListener {
            onDoorbellPressed()
        }
    }

    private fun onDoorbellPressed() {
        binding.btnDoorbell.animate()
            .scaleX(0.9f).scaleY(0.9f).setDuration(100)
            .withEndAction {
                binding.btnDoorbell.animate()
                    .scaleX(1f).scaleY(1f).setDuration(100).start()
            }.start()

        if (!ServerRegistration.isConnected()) {
            setIndicator(ConnectionState.DISCONNECTED)
            Toast.makeText(this, "Server not connected", Toast.LENGTH_SHORT).show()
            return
        }

        ServerRegistration.notifyDoorbellPress()

        val intent = Intent(this@MainActivity, CallActivity::class.java)
        startActivity(intent)
    }

    private fun getLocalIpAddress(): String {
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces()
            while (interfaces.hasMoreElements()) {
                val iface = interfaces.nextElement()
                val addresses = iface.inetAddresses
                while (addresses.hasMoreElements()) {
                    val addr = addresses.nextElement()
                    if (!addr.isLoopbackAddress && addr is Inet4Address) {
                        return addr.hostAddress ?: "unknown"
                    }
                }
            }
        } catch (_: Exception) { }
        return "unknown"
    }

    private fun getServerUrl(): String {
        val prefs = getSharedPreferences("doorbell_prefs", MODE_PRIVATE)
        return prefs.getString("server_url", BuildConfig.DEFAULT_SERVER_URL) ?: BuildConfig.DEFAULT_SERVER_URL
    }

    private fun requestBatteryOptimizationExemption() {
        val pm = getSystemService(PowerManager::class.java)
        if (!pm.isIgnoringBatteryOptimizations(packageName)) {
            val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                data = Uri.parse("package:$packageName")
            }
            startActivity(intent)
        }
    }

    private fun setScreenBrightness(brightness: Float) {
        window.attributes = window.attributes.apply {
            screenBrightness = brightness
        }
    }

    // ── Kiosk / Lock-Task Mode ──────────────────────────────────────────

    /**
     * If this app has been set as device owner, enable lock-task mode.
     * This pins the app to the screen: no status bar, no notifications,
     * no recent-apps, no home button — a true kiosk appliance.
     *
     * To set as device owner (one-time, via ADB with phone USB-connected):
     *   adb shell dpm set-device-owner com.doorbell.app/.receiver.DoorbellDeviceAdmin
     */
    private fun enterKioskMode() {
        val dpm = getSystemService(DevicePolicyManager::class.java)
        val adminComponent = DoorbellDeviceAdmin.getComponentName(this)

        if (!dpm.isDeviceOwnerApp(packageName)) {
            Log.i(TAG, "Not device owner — skipping kiosk mode. " +
                    "Run: adb shell dpm set-device-owner com.doorbell.app/.receiver.DoorbellDeviceAdmin")
            return
        }

        // Allow our package (and only our package) to enter lock-task mode
        dpm.setLockTaskPackages(adminComponent, arrayOf(packageName))

        // Configure which system features remain available (almost none)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            dpm.setLockTaskFeatures(adminComponent,
                DevicePolicyManager.LOCK_TASK_FEATURE_NONE)
        }

        // Block user additions, app installs, and safe-boot
        setUserRestrictions(dpm, adminComponent)

        // Pin this activity
        startLockTask()
        Log.i(TAG, "Kiosk mode active — app is pinned")
    }

    /**
     * Apply user restrictions that turn the phone into a locked-down appliance.
     *
     * NOTE: We intentionally do NOT set DISALLOW_INSTALL_APPS here.
     * The Play Store and package installer are already disabled via
     * kiosk-setup.sh, and setting that restriction also blocks ADB
     * installs — which makes it impossible to push updates over USB.
     */
    private fun setUserRestrictions(dpm: DevicePolicyManager, admin: ComponentName) {
        val restrictions = listOf(
            UserManager.DISALLOW_INSTALL_UNKNOWN_SOURCES,
            UserManager.DISALLOW_SAFE_BOOT,
            UserManager.DISALLOW_ADD_USER,
            UserManager.DISALLOW_MOUNT_PHYSICAL_MEDIA,
        )
        for (r in restrictions) {
            dpm.addUserRestriction(admin, r)
        }
    }

    companion object {
        private const val TAG = "MainActivity"
    }
}
