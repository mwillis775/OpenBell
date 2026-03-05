package com.doorbell.app.stream

import android.util.Log
import fi.iki.elonen.NanoHTTPD
import java.io.ByteArrayInputStream
import java.io.PipedInputStream
import java.io.PipedOutputStream
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Embedded HTTP server that serves the phone camera as an MJPEG stream.
 *
 * Endpoints:
 *  GET /video   — multipart MJPEG stream (for the PC server's vision engine)
 *  GET /snapshot — single JPEG frame
 */
class MjpegStreamServer(port: Int = 8080) : NanoHTTPD(port) {

    companion object {
        private const val TAG = "MjpegStreamServer"
        private const val BOUNDARY = "mjpegframe"
    }

    @Volatile
    private var latestFrame: ByteArray? = null
    private val clients = CopyOnWriteArrayList<PipedOutputStream>()

    /**
     * Called by CameraStreamService every time a new JPEG frame is available.
     */
    fun onFrame(jpeg: ByteArray) {
        latestFrame = jpeg
        // Push to all connected MJPEG stream clients
        val deadClients = mutableListOf<PipedOutputStream>()
        for (client in clients) {
            try {
                val header = "--$BOUNDARY\r\nContent-Type: image/jpeg\r\nContent-Length: ${jpeg.size}\r\n\r\n"
                client.write(header.toByteArray())
                client.write(jpeg)
                client.write("\r\n".toByteArray())
                client.flush()
            } catch (e: Exception) {
                deadClients.add(client)
            }
        }
        clients.removeAll(deadClients.toSet())
    }

    override fun serve(session: IHTTPSession): Response {
        val uri = session.uri.lowercase()
        return when {
            uri == "/snapshot" || uri == "/shot.jpg" -> serveSnapshot()
            uri == "/video" || uri == "/" -> serveMjpegStream()
            else -> newFixedLengthResponse(
                Response.Status.NOT_FOUND, MIME_PLAINTEXT, "Not found. Use /video or /snapshot"
            )
        }
    }

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

    private fun serveMjpegStream(): Response {
        val pipedOut = PipedOutputStream()
        val pipedIn = PipedInputStream(pipedOut, 512 * 1024)
        clients.add(pipedOut)

        Log.i(TAG, "MJPEG client connected (total: ${clients.size})")

        // Send an initial frame if available so the client gets something immediately
        latestFrame?.let { frame ->
            try {
                val header = "--$BOUNDARY\r\nContent-Type: image/jpeg\r\nContent-Length: ${frame.size}\r\n\r\n"
                pipedOut.write(header.toByteArray())
                pipedOut.write(frame)
                pipedOut.write("\r\n".toByteArray())
                pipedOut.flush()
            } catch (_: Exception) {}
        }

        return newChunkedResponse(
            Response.Status.OK,
            "multipart/x-mixed-replace; boundary=$BOUNDARY",
            pipedIn
        )
    }

    override fun stop() {
        super.stop()
        for (client in clients) {
            try { client.close() } catch (_: Exception) {}
        }
        clients.clear()
        Log.i(TAG, "MJPEG server stopped")
    }
}
