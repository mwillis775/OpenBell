package com.doorbell.app.network

import android.util.Log
import com.google.gson.Gson
import com.google.gson.JsonObject
import okhttp3.*
import okio.ByteString.Companion.toByteString
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.TimeUnit

/**
 * WebSocket-based communication with the Rust coordination server.
 *
 * Maintains a persistent WebSocket connection. All messages (register,
 * doorbell press, audio ready, heartbeat, end call) flow over WebSocket
 * instead of HTTP — matching the Rust server's WebSocket-first protocol.
 */
object ServerRegistration {

    private const val TAG = "ServerRegistration"

    private val client = OkHttpClient.Builder()
        .readTimeout(0, TimeUnit.SECONDS)
        .pingInterval(30, TimeUnit.SECONDS)
        .build()

    private val gson = Gson()
    private var webSocket: WebSocket? = null
    @Volatile private var connected = false
    private var serverUrl: String? = null

    /** Guards against reconnect storms — only one reconnect in flight at a time */
    @Volatile private var reconnectPending = false

    /** The current WebSocket instance — used to ignore callbacks from stale sockets */
    @Volatile private var currentWs: WebSocket? = null

    /** Stored registration info so we can re-register on every reconnect */
    @Volatile private var registeredIp: String? = null
    @Volatile private var registeredPort: Int = 8080

    /** Stored audio-ready port so we re-send on every reconnect */
    @Volatile private var audioReadyPort: Int? = null

    /** Thread-safe listeners for incoming server messages */
    private val listeners = CopyOnWriteArrayList<(JsonObject) -> Unit>()

    // ── Connection management ──

    @Synchronized
    fun connect(serverUrl: String) {
        // Neutralize the old socket identity BEFORE cancelling — so its
        // onFailure callback (fired asynchronously on OkHttp's thread)
        // will see ws !== currentWs and bail out instead of scheduling
        // another reconnect.
        currentWs = null
        try { webSocket?.cancel() } catch (_: Exception) {}
        webSocket = null
        connected = false
        reconnectPending = false  // Allow future scheduleReconnect() if this attempt fails

        this.serverUrl = serverUrl
        val wsUrl = serverUrl
            .replace("http://", "ws://")
            .replace("https://", "wss://") + "/ws"
        Log.i(TAG, "Connecting WebSocket: $wsUrl")

        val request = Request.Builder().url(wsUrl).build()
        val newWs = client.newWebSocket(request, object : WebSocketListener() {
            override fun onOpen(ws: WebSocket, response: Response) {
                // Ignore callbacks from stale sockets
                if (ws !== currentWs) return
                Log.i(TAG, "WebSocket connected")
                connected = true
                reconnectPending = false  // Cancel any pending reconnect timer
                // Auto-register on every connect/reconnect
                val ip = registeredIp
                if (ip != null) {
                    Log.i(TAG, "Auto-registering on reconnect: $ip:$registeredPort")
                    register(ip, registeredPort)
                }
                // Re-send audio-ready so the server always knows our UDP port
                val port = audioReadyPort
                if (port != null) {
                    Log.i(TAG, "Re-sending audio_ready: UDP port $port")
                    sendAudioReady(port)
                }
            }

            override fun onMessage(ws: WebSocket, text: String) {
                if (ws !== currentWs) return
                Log.d(TAG, "WS recv: $text")
                try {
                    val msg = gson.fromJson(text, JsonObject::class.java)
                    listeners.forEach { it(msg) }
                } catch (e: Exception) {
                    Log.w(TAG, "Bad WS message: $text", e)
                }
            }

            override fun onClosing(ws: WebSocket, code: Int, reason: String) {
                if (ws !== currentWs) return
                Log.i(TAG, "WebSocket closing: $code $reason")
                ws.close(1000, null)
            }

            override fun onClosed(ws: WebSocket, code: Int, reason: String) {
                // Ignore callbacks from stale sockets we already cancelled
                if (ws !== currentWs) return
                Log.i(TAG, "WebSocket closed: $code $reason")
                connected = false
                webSocket = null
                currentWs = null
                scheduleReconnect()
            }

            override fun onFailure(ws: WebSocket, t: Throwable, response: Response?) {
                if (ws !== currentWs) return
                Log.e(TAG, "WebSocket failure: ${t.message}")
                connected = false
                webSocket = null
                currentWs = null
                scheduleReconnect()
            }
        })
        webSocket = newWs
        currentWs = newWs
    }

    private fun scheduleReconnect() {
        val url = serverUrl ?: return
        // Prevent multiple reconnect threads from piling up
        if (reconnectPending) return
        reconnectPending = true
        Thread {
            try {
                Thread.sleep(3000)
            } catch (_: InterruptedException) {
                reconnectPending = false
                return@Thread
            }
            // If we're already connected (e.g. a new connect() succeeded
            // while we were sleeping), don't clobber the working connection.
            if (connected) {
                Log.i(TAG, "Skipping reconnect — already connected")
                reconnectPending = false
                return@Thread
            }
            Log.i(TAG, "Attempting WebSocket reconnect...")
            connect(url)
        }.start()
    }

    @Synchronized
    fun disconnect() {
        serverUrl = null
        webSocket?.close(1000, "disconnect")
        webSocket = null
        connected = false
    }

    fun isConnected(): Boolean = connected

    // ── Listener management ──

    fun addListener(listener: (JsonObject) -> Unit) {
        listeners.add(listener)
    }

    fun removeListener(listener: (JsonObject) -> Unit) {
        listeners.remove(listener)
    }

    // ── Send messages ──

    private fun send(data: Map<String, Any>) {
        val json = gson.toJson(data)
        val sent = webSocket?.send(json) ?: false
        if (!sent) Log.w(TAG, "WS send failed: $json")
        else Log.d(TAG, "WS sent: $json")
    }

    /** Send a camera JPEG frame as a binary WebSocket message */
    fun sendFrame(jpeg: ByteArray) {
        webSocket?.send(jpeg.toByteString())
    }

    /** Register this device with the server */
    fun register(deviceIp: String, streamPort: Int = 8080) {
        // Store for auto-register on reconnect
        registeredIp = deviceIp
        registeredPort = streamPort
        send(mapOf(
            "type" to "register",
            "device_ip" to deviceIp,
            "stream_url" to "http://$deviceIp:$streamPort/video",
            "capabilities" to listOf("camera", "doorbell_button", "audio_playback"),
            "device_type" to "doorbell",
            "device_name" to "Front Door"
        ))
    }

    /** Notify server that doorbell button was pressed */
    fun notifyDoorbellPress() {
        send(mapOf("type" to "doorbell_press"))
    }

    /** Tell server we're ready to receive audio on this UDP port */
    fun sendAudioReady(udpPort: Int) {
        audioReadyPort = udpPort
        send(mapOf("type" to "audio_ready", "udp_port" to udpPort))
    }

    /** Send heartbeat */
    fun sendHeartbeat() {
        send(mapOf("type" to "heartbeat"))
    }

    /** Tell server to end the current call */
    fun endCall() {
        send(mapOf("type" to "end_call"))
    }
}
