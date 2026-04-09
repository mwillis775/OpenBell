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
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import androidx.lifecycle.LifecycleRegistry
import com.doorbell.app.R
import com.doorbell.app.network.ServerRegistration
import com.doorbell.app.stream.MjpegStreamServer
import com.doorbell.app.ui.MainActivity
import com.google.gson.JsonObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executors

/**
 * Foreground service that captures camera frames and serves them as MJPEG via
 * an embedded HTTP server. Also records video clips when person presence is
 * detected, and stores snapshots/videos on the phone's internal storage.
 *
 * Optimised for low-latency, low-bandwidth LAN streaming:
 *  - YUV_420_888 → NV21 → YuvImage.compressToJpeg  (hardware path)
 *  - JPEG quality 40 for stream — plenty for a doorbell over LAN
 *  - Capped at 5 fps to save CPU / battery / bandwidth
 *  - 480×360 resolution for stream — enough for person-detection
 *  - HD video recording via CameraX VideoCapture when people are detected
 */
class CameraStreamService : Service(), LifecycleOwner {

    companion object {
        private const val TAG = "CameraStreamService"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "camera_stream"
        const val EXTRA_PORT = "stream_port"
        const val DEFAULT_PORT = 8080

        /** Target FPS cap (camera may deliver faster; we skip surplus frames) */
        private const val TARGET_FPS = 5
        private const val MIN_FRAME_INTERVAL_NS = 1_000_000_000L / TARGET_FPS

        /** JPEG quality — 40 is plenty for person detection over LAN */
        private const val JPEG_QUALITY = 40

        /** JPEG quality for saved snapshots — high quality for review */
        private const val SNAPSHOT_QUALITY = 95
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

    // ── Video recording ──
    private var videoCapture: VideoCapture<Recorder>? = null
    private var activeRecording: Recording? = null
    @Volatile private var isRecording = false
    private var videoSupported = false
    private var currentVideoFile: File? = null

    /** Set to a File to capture the next frame as a high-quality snapshot */
    @Volatile private var pendingSnapshotFile: File? = null

    /** Listener for server messages (person detected/left) */
    private val serverListener: (JsonObject) -> Unit = { msg -> onServerMessage(msg) }

    override val lifecycle: Lifecycle
        get() = lifecycleRegistry

    override fun onCreate() {
        super.onCreate()
        lifecycleRegistry = LifecycleRegistry(this)
        lifecycleRegistry.currentState = Lifecycle.State.CREATED
        MediaStorageManager.init(this)
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
            ServerRegistration.addListener(serverListener)
        }

        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        ServerRegistration.removeListener(serverListener)
        stopVideoRecording()
        lifecycleRegistry.currentState = Lifecycle.State.DESTROYED
        mjpegServer?.stop()
        mjpegServer = null
        cameraExecutor.shutdown()
        Log.i(TAG, "Camera stream service destroyed")
        super.onDestroy()
    }

    // ── Person detection events → recording ──

    private fun onServerMessage(msg: JsonObject) {
        val type = msg.get("type")?.asString ?: return
        when (type) {
            "person_detected" -> {
                Log.i(TAG, "Person detected — starting video capture")
                startVideoCapture()
            }
            "person_left" -> {
                Log.i(TAG, "Person left — stopping video capture")
                stopVideoRecording()
            }
        }
    }

    /**
     * Start recording a video clip and save a snapshot as thumbnail.
     */
    private fun startVideoCapture() {
        if (isRecording) return

        if (videoSupported && videoCapture != null) {
            // Save snapshot (serves as thumbnail for the video)
            val videoFile = MediaStorageManager.generateVideoFile()
            currentVideoFile = videoFile
            val thumbFile = MediaStorageManager.thumbnailForVideo(videoFile)
            pendingSnapshotFile = thumbFile

            val outputOptions = FileOutputOptions.Builder(videoFile).build()
            try {
                activeRecording = videoCapture!!.output
                    .prepareRecording(this, outputOptions)
                    .start(ContextCompat.getMainExecutor(this)) { event ->
                        when (event) {
                            is VideoRecordEvent.Finalize -> {
                                if (event.hasError()) {
                                    Log.e(TAG, "Video recording error: ${event.error}")
                                    videoFile.delete()
                                    thumbFile.delete()
                                } else {
                                    Log.i(TAG, "Video saved: ${videoFile.name} (${videoFile.length() / 1024}KB)")
                                }
                                isRecording = false
                                currentVideoFile = null
                            }
                        }
                    }
                isRecording = true
                Log.i(TAG, "Recording started: ${videoFile.name}")
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start recording: ${e.message}")
                // Fall back to snapshot only
                pendingSnapshotFile = MediaStorageManager.generateSnapshotFile()
            }
        } else {
            // VideoCapture not available — save snapshot only
            Log.i(TAG, "Video recording not available, saving snapshot")
            pendingSnapshotFile = MediaStorageManager.generateSnapshotFile()
        }
    }

    private fun stopVideoRecording() {
        if (!isRecording) return
        try {
            activeRecording?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "Error stopping recording: ${e.message}")
        }
        activeRecording = null
    }

    // ── MJPEG server ──

    private fun startMjpegServer() {
        try {
            mjpegServer = MjpegStreamServer(streamPort).also { it.start() }
            Log.i(TAG, "MJPEG server started on port $streamPort")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start MJPEG server: ${e.message}")
        }
    }

    fun getMjpegServer(): MjpegStreamServer? = mjpegServer

    // ── Camera ──

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

                // Try binding VideoCapture alongside ImageAnalysis for HD recording
                try {
                    val recorder = Recorder.Builder()
                        .setQualitySelector(
                            QualitySelector.from(
                                Quality.HD,
                                androidx.camera.video.FallbackStrategy.higherQualityOrLowerThan(Quality.HD)
                            )
                        )
                        .build()
                    val videoCaptureUseCase = VideoCapture.withOutput(recorder)
                    cameraProvider.bindToLifecycle(this, cameraSelector, imageAnalysis, videoCaptureUseCase)
                    videoCapture = videoCaptureUseCase
                    videoSupported = true
                    Log.i(TAG, "Camera bound — ImageAnalysis (640×480) + VideoCapture (HD), ${TARGET_FPS}fps stream")
                } catch (e: Exception) {
                    // Fallback: ImageAnalysis only (snapshots will still work)
                    Log.w(TAG, "VideoCapture not supported, falling back to ImageAnalysis only: ${e.message}")
                    cameraProvider.unbindAll()
                    cameraProvider.bindToLifecycle(this, cameraSelector, imageAnalysis)
                    videoSupported = false
                    Log.i(TAG, "Camera bound — ImageAnalysis only (640×480), ${TARGET_FPS}fps stream")
                }
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

            val width = imageProxy.width
            val height = imageProxy.height
            val nv21 = yuvToNv21(imageProxy) ?: return
            val yuvImage = YuvImage(nv21, ImageFormat.NV21, width, height, null)
            val rect = Rect(0, 0, width, height)

            // Encode low-quality JPEG for streaming
            jpegBuffer.reset()
            if (yuvImage.compressToJpeg(rect, JPEG_QUALITY, jpegBuffer)) {
                val jpeg = jpegBuffer.toByteArray()
                mjpegServer?.onFrame(jpeg)
                ServerRegistration.sendFrame(jpeg)
            }

            // Save high-quality snapshot if requested
            val snapshotFile = pendingSnapshotFile
            if (snapshotFile != null) {
                pendingSnapshotFile = null
                try {
                    val hqOut = ByteArrayOutputStream(131_072)
                    if (yuvImage.compressToJpeg(rect, SNAPSHOT_QUALITY, hqOut)) {
                        snapshotFile.parentFile?.mkdirs()
                        snapshotFile.writeBytes(hqOut.toByteArray())
                        Log.i(TAG, "Snapshot saved: ${snapshotFile.name} (${hqOut.size() / 1024}KB)")
                    }
                } catch (e: Exception) {
                    Log.e(TAG, "Snapshot save failed: ${e.message}")
                }
            }
        } catch (e: Exception) {
            Log.e(TAG, "Frame processing error: ${e.message}")
        } finally {
            imageProxy.close()
        }
    }

    /**
     * Convert a YUV_420_888 [ImageProxy] to a tightly-packed NV21 byte array.
     *
     * Key correctness detail: the Y and UV plane buffers may have a
     * rowStride larger than the image width (padding bytes at end of each
     * row). We must strip that padding when building the tightly-packed
     * NV21 array, otherwise every row shifts and produces coloured bars.
     */
    private fun yuvToNv21(image: ImageProxy): ByteArray? {
        val width  = image.width
        val height = image.height

        val yPlane = image.planes[0]
        val uPlane = image.planes[1]
        val vPlane = image.planes[2]

        val yBuffer = yPlane.buffer.duplicate()
        val uBuffer = uPlane.buffer.duplicate()
        val vBuffer = vPlane.buffer.duplicate()

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
            for (row in 0 until uvHeight) {
                for (col in 0 until uvWidth) {
                    val idx = row * uvRowStride + col * uvPixelStride
                    nv21[uvOffset++] = vBuffer.get(idx)
                    nv21[uvOffset++] = uBuffer.get(idx)
                }
            }
        }

        return nv21
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
