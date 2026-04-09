package com.doorbell.app.service

import android.content.Context
import android.util.Log
import java.io.File
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Manages doorbell media storage on the phone's internal storage.
 *
 * Files are stored in `getFilesDir()/doorbell_media/`:
 *  - `snap_YYYYMMDD_HHMMSS.jpg`  — standalone snapshots (fallback when video unavailable)
 *  - `video_YYYYMMDD_HHMMSS.mp4` — person presence recordings
 *  - `video_YYYYMMDD_HHMMSS_thumb.jpg` — thumbnail for the corresponding video
 *
 * Automatically deletes files older than 30 days.
 */
object MediaStorageManager {

    private const val TAG = "MediaStorage"
    private const val MEDIA_DIR = "doorbell_media"
    private const val MAX_AGE_MS = 30L * 24 * 60 * 60 * 1000 // 30 days

    private lateinit var mediaDir: File
    private val tsFormat = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.US)
    private val cleanupExecutor = Executors.newSingleThreadScheduledExecutor()

    fun init(context: Context) {
        mediaDir = File(context.filesDir, MEDIA_DIR)
        mediaDir.mkdirs()
        Log.i(TAG, "Media dir: ${mediaDir.absolutePath}")

        // Cleanup on startup
        cleanupOldFiles()

        // Schedule daily cleanup
        cleanupExecutor.scheduleAtFixedRate(
            { cleanupOldFiles() }, 24, 24, TimeUnit.HOURS
        )
    }

    fun getMediaDir(): File = mediaDir

    /** Generate a timestamped filename base (e.g., "20260409_120000") */
    private fun timestamp(): String = tsFormat.format(Date())

    fun generateSnapshotFile(): File = File(mediaDir, "snap_${timestamp()}.jpg")

    fun generateVideoFile(): File = File(mediaDir, "video_${timestamp()}.mp4")

    fun thumbnailForVideo(videoFile: File): File =
        File(mediaDir, videoFile.nameWithoutExtension + "_thumb.jpg")

    /**
     * Metadata for a single media file.
     */
    data class MediaItem(
        val filename: String,
        val type: String,       // "snapshot" or "video"
        val size: Long,
        val timestamp: Long,    // epoch millis
        val thumbnail: String?, // filename of thumbnail (for videos)
    )

    /**
     * List all media files, newest first.
     * Excludes thumbnails (they're referenced by their parent video).
     */
    fun listMedia(): List<MediaItem> {
        if (!::mediaDir.isInitialized || !mediaDir.exists()) return emptyList()
        return mediaDir.listFiles()
            ?.filter { it.isFile && !it.name.endsWith("_thumb.jpg") }
            ?.filter { it.name.endsWith(".jpg") || it.name.endsWith(".mp4") }
            ?.sortedByDescending { it.lastModified() }
            ?.map { file ->
                val type = if (file.name.startsWith("video_")) "video" else "snapshot"
                val thumb = if (type == "video") {
                    val tf = File(mediaDir, file.nameWithoutExtension + "_thumb.jpg")
                    if (tf.exists()) tf.name else null
                } else null
                MediaItem(file.name, type, file.length(), file.lastModified(), thumb)
            } ?: emptyList()
    }

    /**
     * Get a file by name, with path-traversal protection.
     */
    fun getFile(filename: String): File? {
        val sanitized = File(filename).name
        if (sanitized != filename) return null // reject path traversal
        val file = File(mediaDir, sanitized)
        return if (file.exists() && file.canonicalPath.startsWith(mediaDir.canonicalPath)) file else null
    }

    /**
     * Delete a media file and its thumbnail.
     */
    fun deleteFile(filename: String): Boolean {
        val file = getFile(filename) ?: return false
        // Also delete associated thumbnail
        val thumbFile = File(mediaDir, File(filename).nameWithoutExtension + "_thumb.jpg")
        if (thumbFile.exists()) thumbFile.delete()
        return file.delete()
    }

    /**
     * Remove files older than 30 days.
     */
    fun cleanupOldFiles() {
        if (!::mediaDir.isInitialized || !mediaDir.exists()) return
        val cutoff = System.currentTimeMillis() - MAX_AGE_MS
        var count = 0
        mediaDir.listFiles()?.forEach { file ->
            if (file.isFile && file.lastModified() < cutoff) {
                file.delete()
                count++
            }
        }
        if (count > 0) Log.i(TAG, "Cleaned up $count old media files")
    }
}
