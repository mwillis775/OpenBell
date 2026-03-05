package com.doorbell.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.Matrix
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
 */
class CameraStreamService : Service(), LifecycleOwner {

    companion object {
        private const val TAG = "CameraStreamService"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "camera_stream"
        const val EXTRA_PORT = "stream_port"
        const val DEFAULT_PORT = 8080
    }

    private lateinit var lifecycleRegistry: LifecycleRegistry
    private var mjpegServer: MjpegStreamServer? = null
    private val cameraExecutor = Executors.newSingleThreadExecutor()
    private var streamPort = DEFAULT_PORT

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

        startMjpegServer()
        startCamera()

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
                    .setOutputImageFormat(ImageAnalysis.OUTPUT_IMAGE_FORMAT_RGBA_8888)
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

                Log.i(TAG, "Front camera bound — streaming at 640x480")
            } catch (e: Exception) {
                Log.e(TAG, "Camera bind failed: ${e.message}")
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun processFrame(imageProxy: ImageProxy) {
        try {
            val jpeg = imageProxyToJpeg(imageProxy)
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
     * Convert RGBA_8888 ImageProxy to JPEG bytes.
     * CameraX delivers RGBA directly — no manual YUV conversion needed.
     * We also apply the rotation from imageProxy.imageInfo so the image is upright.
     */
    private fun imageProxyToJpeg(image: ImageProxy): ByteArray? {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val rowPadding = rowStride - pixelStride * image.width

        // Create bitmap from the RGBA buffer
        val bitmapWidth = image.width + rowPadding / pixelStride
        val bitmap = Bitmap.createBitmap(bitmapWidth, image.height, Bitmap.Config.ARGB_8888)
        buffer.rewind()
        bitmap.copyPixelsFromBuffer(buffer)

        // Crop to actual image width and apply rotation
        val rotation = image.imageInfo.rotationDegrees
        val matrix = Matrix()
        if (rotation != 0) {
            matrix.postRotate(rotation.toFloat())
        }
        // Front camera is mirrored — flip horizontally so text reads correctly
        matrix.postScale(-1f, 1f)

        val oriented = Bitmap.createBitmap(bitmap, 0, 0, image.width, image.height, matrix, true)
        if (oriented !== bitmap) bitmap.recycle()

        // Compress to JPEG
        val out = ByteArrayOutputStream(oriented.byteCount / 8)
        oriented.compress(Bitmap.CompressFormat.JPEG, 75, out)
        oriented.recycle()

        return out.toByteArray()
    }

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
