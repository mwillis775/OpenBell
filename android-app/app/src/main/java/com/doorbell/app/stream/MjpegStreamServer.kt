package com.doorbell.app.stream

import android.util.Log
import com.doorbell.app.service.MediaStorageManager
import com.google.gson.Gson
import fi.iki.elonen.NanoHTTPD
import java.io.ByteArrayInputStream
import java.io.FileInputStream
import java.io.InputStream
import java.util.concurrent.TimeUnit
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * Embedded HTTP server that serves the phone camera as an MJPEG stream
 * and provides access to stored media files (snapshots + video recordings).
 *
 * Endpoints:
 *  GET /video       — multipart MJPEG stream (for the PC server's vision engine)
 *  GET /snapshot    — single JPEG frame
 *  GET /media/list  — JSON list of stored media files
 *  GET /media/file/<name>  — download a media file
 *  POST /media/delete/<name> — delete a media file
 */
class MjpegStreamServer(port: Int = 8080) : NanoHTTPD(port) {

    companion object {
        private const val TAG = "MjpegStreamServer"
        private const val BOUNDARY = "mjpegframe"
    }

    private val frameLock = ReentrantLock()
    private val frameAvailable = frameLock.newCondition()
    private val gson = Gson()

    @Volatile
    private var latestFrame: ByteArray? = null

    @Volatile
    private var frameSeq: Long = 0          // incremented on every new frame

    @Volatile
    private var clientCount = 0

    /**
     * Called by CameraStreamService every time a new JPEG frame is available.
     */
    fun onFrame(jpeg: ByteArray) {
        frameLock.withLock {
            latestFrame = jpeg
            frameSeq++
            frameAvailable.signalAll()
        }
    }

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri.lowercase()
        val method = session.method

        return when {
            // ── Media endpoints ──
            uri == "/media/list" -> serveMediaList()
            uri.startsWith("/media/file/") -> serveMediaFile(session.uri)
            uri.startsWith("/media/delete/") && method == Method.POST -> deleteMediaFile(session.uri)

            // ── Camera stream endpoints ──
            uri == "/snapshot" || uri == "/shot.jpg" -> serveSnapshot()
            uri == "/video" || uri == "/" -> serveMjpegStream()

            else -> newFixedLengthResponse(
                Response.Status.NOT_FOUND, MIME_PLAINTEXT,
                "Not found. Use /video, /snapshot, or /media/list"
            )
        }
    }

    // ── Media serving ──

    private fun serveMediaList(): Response {
        val items = MediaStorageManager.listMedia()
        val json = gson.toJson(mapOf("media" to items))
        return newFixedLengthResponse(
            Response.Status.OK, "application/json", json
        )
    }

    private fun serveMediaFile(uri: String): Response {
        // Extract filename from /media/file/<name>
        val filename = uri.removePrefix("/media/file/").removePrefix("/")
        if (filename.isEmpty()) {
            return newFixedLengthResponse(
                Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "Missing filename"
            )
        }

        val file = MediaStorageManager.getFile(filename)
            ?: return newFixedLengthResponse(
                Response.Status.NOT_FOUND, MIME_PLAINTEXT, "File not found"
            )

        val contentType = when {
            filename.endsWith(".jpg") || filename.endsWith(".jpeg") -> "image/jpeg"
            filename.endsWith(".mp4") -> "video/mp4"
            else -> "application/octet-stream"
        }

        return newFixedLengthResponse(
            Response.Status.OK, contentType,
            FileInputStream(file), file.length()
        )
    }

    private fun deleteMediaFile(uri: String): Response {
        val filename = uri.removePrefix("/media/delete/").removePrefix("/")
        if (filename.isEmpty()) {
            return newFixedLengthResponse(
                Response.Status.BAD_REQUEST, MIME_PLAINTEXT, "Missing filename"
            )
        }

        val deleted = MediaStorageManager.deleteFile(filename)
        val json = gson.toJson(mapOf("deleted" to deleted))
        return newFixedLengthResponse(
            Response.Status.OK, "application/json", json
        )
    }

    // ── Camera stream ──

    private fun serveSnapshot(): Response {
        val frame = latestFrame
            ?: return newFixedLengthResponse(
                Response.Status.SERVICE_UNAVAILABLE, MIME_PLAINTEXT, "No frame available yet"
            )
        return newFixedLengthResponse(
            Response.Status.OK, "image/jpeg",
            ByteArrayInputStream(frame), frame.size.toLong()
        )
    }

    /**
     * Each connected client gets a blocking [InputStream] that yields MJPEG
     * multipart chunks.  The InputStream blocks on [read] until a new frame
     * is signalled.  If the client's read is slow, it simply skips to the
     * latest frame — no buffering, no pipe overflow.
     */
    private fun serveMjpegStream(): Response {
        clientCount++
        Log.i(TAG, "MJPEG client connected (total: $clientCount)")

        val stream = MjpegClientStream()
        return newChunkedResponse(
            Response.Status.OK,
            "multipart/x-mixed-replace; boundary=$BOUNDARY",
            stream
        )
    }

    /**
     * A per-client [InputStream] that blocks until the next frame is available
     * and then returns the MJPEG multipart chunk bytes.
     *
     * - No internal buffer accumulation (always serves the *latest* frame).
     * - Automatically skips frames if the reader is slower than the camera.
     * - When the client disconnects NanoHTTPD closes the stream, which sets
     *   [closed] and wakes the lock so the thread can exit cleanly.
     */
    private inner class MjpegClientStream : InputStream() {
        private var lastSeenSeq: Long = -1
        private var pendingBytes: ByteArray? = null
        private var pendingPos = 0

        @Volatile
        private var closed = false

        /** Timeout for waiting on new frames — prevents threads from blocking forever */
        private val AWAIT_TIMEOUT_MS = 2000L

        override fun read(): Int {
            while (true) {
                if (closed) return -1

                // Serve remaining bytes of the current chunk first
                val buf = pendingBytes
                if (buf != null && pendingPos < buf.size) {
                    return buf[pendingPos++].toInt() and 0xFF
                }

                // Current chunk exhausted — wait for a new frame
                pendingBytes = null
                pendingPos = 0

                frameLock.withLock {
                    // Wait until there's a frame newer than the last one we sent
                    // Use a timeout so we don't block forever on dead connections
                    var waited = false
                    while (frameSeq <= lastSeenSeq && !closed) {
                        try {
                            if (!frameAvailable.await(AWAIT_TIMEOUT_MS, TimeUnit.MILLISECONDS)) {
                                // Timed out — if no frames have arrived at all, keep waiting
                                // (camera might be initializing). Otherwise the stream is healthy
                                // and we just haven't hit the next frame yet.
                                if (waited) continue
                                waited = true
                                continue
                            }
                        } catch (_: InterruptedException) {
                            closed = true
                            return -1
                        }
                    }
                    if (closed) return -1

                    val frame = latestFrame ?: return -1
                    lastSeenSeq = frameSeq

                    // Build the multipart chunk
                    val header = "--$BOUNDARY\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.size}\r\n\r\n"
                    val chunk = ByteArray(header.length + frame.size + 2)
                    System.arraycopy(header.toByteArray(), 0, chunk, 0, header.length)
                    System.arraycopy(frame, 0, chunk, header.length, frame.size)
                    chunk[chunk.size - 2] = '\r'.code.toByte()
                    chunk[chunk.size - 1] = '\n'.code.toByte()
                    pendingBytes = chunk
                    pendingPos = 0
                }
            }
        }

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (closed) return -1
            if (len == 0) return 0

            // Fast bulk-read path
            val buf = pendingBytes
            if (buf != null && pendingPos < buf.size) {
                val available = buf.size - pendingPos
                val n = minOf(available, len)
                System.arraycopy(buf, pendingPos, b, off, n)
                pendingPos += n
                return n
            }

            // No pending data — do a blocking single-byte read to fill the next chunk
            val first = read()
            if (first == -1) return -1
            b[off] = first.toByte()

            // Now drain what we can without blocking
            val buf2 = pendingBytes ?: return 1
            val available = buf2.size - pendingPos
            val n = minOf(available, len - 1)
            if (n > 0) {
                System.arraycopy(buf2, pendingPos, b, off + 1, n)
                pendingPos += n
            }
            return n + 1
        }

        override fun available(): Int {
            val buf = pendingBytes ?: return 0
            return buf.size - pendingPos
        }

        override fun close() {
            closed = true
            clientCount--
            Log.i(TAG, "MJPEG client disconnected (remaining: $clientCount)")
            // Wake any thread blocked in await()
            frameLock.withLock { frameAvailable.signalAll() }
        }
    }

    override fun stop() {
        super.stop()
        // Wake all blocked client threads so they exit
        frameLock.withLock { frameAvailable.signalAll() }
        Log.i(TAG, "MJPEG server stopped")
    }
}
