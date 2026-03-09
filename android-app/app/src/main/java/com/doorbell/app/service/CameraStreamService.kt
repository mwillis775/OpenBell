package com.doorbell.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.ImageFormat
import android.graphics.Rect
import android.graphics.YuvImage
import android.os.IBinder
import android.util.Log
import android.util.Size
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.doorbell.app.R
import com.doorbell.app.stream.MjpegStreamServer
import com.doorbell.app.ui.MainActivity
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

/**
 * Foreground service that captures camera frames and serves them as MJPEG via
 * an embedded HTTP server. This runs continuously so the PC server can always
 * pull the camera feed for AI vision.
 *
 * Optimised for low-latency, low-bandwidth LAN streaming:
 *  - YUV_420_888 → NV21 → YuvImage.compressToJpeg  (hardware path, ~3× faster
 *    than the old RGBA → Bitmap → rotate → JPEG path)
 *  - JPEG quality 40 — plenty for a doorbell over LAN (~15-25 KB/frame)
 *  - Capped at 12 fps to save CPU / battery / bandwidth
 *  - 480×360 resolution — enough for person-detection, saves encoding time
 */
class CameraStreamService : Service(), LifecycleOwner {

    companion object {
        private const val TAG = "CameraStreamService"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "camera_stream"
        const val EXTRA_PORT = "stream_port"
        const val DEFAULT_PORT = 8080

        /** Target FPS cap (camera may deliver faster; we skip surplus frames) */
        private const val TARGET_FPS = 30
        private const val MIN_FRAME_INTERVAL_NS = 1_000_000_000L / TARGET_FPS

        /** JPEG quality — 55 gives clean picture at reasonable bandwidth on LAN */
        private const val JPEG_QUALITY = 55
    }

    private lateinit var lifecycleRegistry: LifecycleRegistry
    private var mjpegServer: MjpegStreamServer? = null
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var streamPort = DEFAULT_PORT
    private var started = false

    /** Timestamp (System.nanoTime) of the last frame we encoded */
    private var lastFrameNs: Long = 0

    /** Reusable output buffer — avoids allocating a new ByteArrayOutputStream every frame */
    private val jpegBuffer = ByteArrayOutputStream(32_768)

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    override fun onCreate() {
        super.onCreate()
        lifecycleRegistry = LifecycleRegistry(this)
        lifecycleRegistry.currentState = Lifecycle.State.CREATED
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        streamPort = intent?.getIntExtra(EXTRA_PORT, DEFAULT_PORT) ?: DEFAULT_PORT

        createNotificationChannel()
        startForeground(NOTIFICATION_ID, buildNotification())

        lifecycleRegistry.currentState = Lifecycle.State.STARTED

        // Guard: only start server + camera once — onStartCommand can be
        // called multiple times (START_STICKY re-delivery, duplicate intents).
        if (!started) {
            started = true
            startMjpegServer()
            startCamera()
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        mjpegServer?.stop()
        mjpegServer = null
        cameraExecutor.shutdown()
        Log.i(TAG, "Camera stream service destroyed")
        super.onDestroy()
    }

    private fun startMjpegServer() {
        try {
            mjpegServer = MjpegStreamServer(streamPort).also { it.start() }
            Log.i(TAG, "MJPEG server started on port $streamPort")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start MJPEG server: ${e.message}")
        }
    }

    private fun startCamera() {
        val cameraProviderFuture = ProcessCameraProvider.getInstance(this)
        cameraProviderFuture.addListener({
            try {
                val cameraProvider = cameraProviderFuture.get()

                val imageAnalysis = ImageAnalysis.Builder()
                    .setTargetResolution(Size(640, 480))
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_YUV_420_888)
                    .build()

                imageAnalysis.setAnalyzer(cameraExecutor) { imageProxy ->
                    processFrame(imageProxy)
                }

                // Use front camera — faces the visitor at the door
                val cameraSelector = CameraSelector.Builder()
                    .requireLensFacing(CameraSelector.LENS_FACING_FRONT)
                    .build()

                cameraProvider.unbindAll()
                cameraProvider.bindToLifecycle(this, cameraSelector, imageAnalysis)

                Log.i(TAG, "Front camera bound — streaming at 640×480, ${TARGET_FPS}fps cap, JPEG q$JPEG_QUALITY")
            } catch (e: Exception) {
                Log.e(TAG, "Camera bind failed: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    // ── Frame processing ─────────────────────────────────────────────────────

    private fun processFrame(imageProxy: ImageProxy) {
        try {
            // FPS cap — skip frame if we're ahead of schedule
            val now = System.nanoTime()
            if (now - lastFrameNs < MIN_FRAME_INTERVAL_NS) {
                return
            }
            lastFrameNs = now

            val jpeg = yuvToJpeg(imageProxy)
            if (jpeg != null) {
                mjpegServer?.onFrame(jpeg)
            }
        } catch (e: Exception) {
            Log.e(TAG, "Frame processing error: ${e.message}")
        } finally {
            imageProxy.close()
        }
    }

    /**
     * Convert a YUV_420_888 [ImageProxy] to JPEG bytes using Android's
     * built-in [YuvImage] compressor (backed by libjpeg-turbo).
     *
     * Key correctness detail: the Y and UV plane buffers may have a
     * rowStride larger than the image width (padding bytes at end of each
     * row). We must strip that padding when building the tightly-packed
     * NV21 array, otherwise every row shifts and produces coloured bars.
     */
    private fun yuvToJpeg(image: ImageProxy): ByteArray? {
        val width  = image.width
        val height = image.height

        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]

        val yBuffer = yPlane.buffer
        val uBuffer = uPlane.buffer
        val vBuffer = vPlane.buffer

        val yRowStride   = yPlane.rowStride
        val uvRowStride  = uPlane.rowStride
        val uvPixelStride = uPlane.pixelStride

        // NV21: W*H luma bytes + W*H/2 interleaved VU chroma bytes
        val nv21 = ByteArray(width * height * 3 / 2)

        // ── Y plane (respect rowStride) ──────────────────────
        if (yRowStride == width) {
            yBuffer.position(0)
            yBuffer.get(nv21, 0, width * height)
        } else {
            for (row in 0 until height) {
                yBuffer.position(row * yRowStride)
                yBuffer.get(nv21, row * width, width)
            }
        }

        // ── UV → interleaved VU (NV21) ──────────────────────
        val uvHeight = height / 2
        val uvWidth  = width / 2
        var uvOffset = width * height

        if (uvPixelStride == 2 && uvRowStride == width) {
            // Fast path: data is already VU-interleaved and tightly packed.
            vBuffer.position(0)
            val toCopy = minOf(vBuffer.remaining(), uvWidth * uvHeight * 2)
            vBuffer.get(nv21, uvOffset, toCopy)
            if (toCopy < uvWidth * uvHeight * 2) {
                // Last U byte may be missing — grab it from U buffer
                nv21[uvOffset + toCopy] = uBuffer.get(uBuffer.limit() - 1)
            }
        } else {
            // General path: handles any rowStride / pixelStride combo.
            // Uses absolute get(index) so buffer position is never touched.
            for (row in 0 until uvHeight) {
                for (col in 0 until uvWidth) {
                    val idx = row * uvRowStride + col * uvPixelStride
                    nv21[uvOffset++] = vBuffer.get(idx)
                    nv21[uvOffset++] = uBuffer.get(idx)
                }
            }
        }

        val yuvImage = YuvImage(nv21, ImageFormat.NV21, width, height, null)

        // No pixel manipulation here — rotation and flip are applied by
        // consumers (CSS transform in Electron, cv2.rotate in CV server)
        // to keep the phone's encode path as fast as possible.
        jpegBuffer.reset()
        if (!yuvImage.compressToJpeg(Rect(0, 0, width, height), JPEG_QUALITY, jpegBuffer)) {
            return null
        }
        return jpegBuffer.toByteArray()
    }

    // ── Notification boilerplate ─────────────────────────────────────────────

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Camera Stream",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Doorbell camera streaming to server"
            setShowBadge(false)
        }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Doorbell Active")
            .setContentText("Camera streaming to server")
            .setSmallIcon(R.drawable.ic_doorbell)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }
}
