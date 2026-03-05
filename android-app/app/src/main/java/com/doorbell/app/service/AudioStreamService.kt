package com.doorbell.app.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.doorbell.app.R
import com.doorbell.app.network.ServerRegistration
import com.doorbell.app.ui.MainActivity
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress

/**
 * Always-on foreground service that:
 * 1. Captures phone mic audio and streams it via UDP to the Rust server (port 5003)
 * 2. Listens for incoming intercom audio from the server (UDP) and plays through speaker
 *
 * Audio format: 48 kHz mono 16-bit PCM, 10ms packets (480 samples = 960 bytes)
 * UDP packet: [4-byte big-endian seq number] [PCM data]
 */
class AudioStreamService : Service() {

    companion object {
        private const val TAG = "AudioStreamSvc"
        const val CHANNEL_ID = "audio_stream_channel"
        const val NOTIFICATION_ID = 3
        const val EXTRA_SERVER_IP = "server_ip"
        const val EXTRA_SERVER_PORT = "server_port"

        private const val SAMPLE_RATE = 48000
        private const val UDP_HEADER_SIZE = 4
        private const val SAMPLES_PER_PACKET = 480
        private const val BYTES_PER_PACKET = SAMPLES_PER_PACKET * 2 // 960 bytes
        private const val SERVER_RECV_PORT = 5003 // server listens for phone audio here
        private const val LISTEN_PORT = 5002      // phone listens for intercom audio here
    }

    @Volatile private var running = false
    private var micThread: Thread? = null
    private var speakerThread: Thread? = null
    private var audioRecord: AudioRecord? = null
    private var audioTrack: AudioTrack? = null
    private var sendSocket: DatagramSocket? = null
    private var recvSocket: DatagramSocket? = null

    private var serverIp: String = ""

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        serverIp = intent?.getStringExtra(EXTRA_SERVER_IP) ?: ""
        if (serverIp.isEmpty()) {
            Log.e(TAG, "No server IP provided, stopping")
            stopSelf()
            return START_NOT_STICKY
        }

        Log.i(TAG, "Starting audio streams, server=$serverIp:$SERVER_RECV_PORT")
        startForeground(NOTIFICATION_ID, buildNotification())

        running = true
        startMicCapture()
        startSpeakerPlayback()

        // Tell server our listening port
        ServerRegistration.sendAudioReady(LISTEN_PORT)

        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        running = false
        micThread?.interrupt()
        speakerThread?.interrupt()
        audioRecord?.let {
            try { it.stop() } catch (_: Exception) {}
            try { it.release() } catch (_: Exception) {}
        }
        audioTrack?.let {
            try { it.stop() } catch (_: Exception) {}
            try { it.release() } catch (_: Exception) {}
        }
        sendSocket?.close()
        recvSocket?.close()
        Log.i(TAG, "Audio streams stopped")
    }

    // ── Mic capture → UDP to server ──

    private fun startMicCapture() {
        micThread = Thread {
            try {
                val minBuf = AudioRecord.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                val bufSize = maxOf(minBuf * 2, BYTES_PER_PACKET * 4)

                val record = AudioRecord(
                    MediaRecorder.AudioSource.MIC,
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_IN_MONO,
                    AudioFormat.ENCODING_PCM_16BIT,
                    bufSize
                )
                audioRecord = record

                if (record.state != AudioRecord.STATE_INITIALIZED) {
                    Log.e(TAG, "AudioRecord failed to initialize")
                    return@Thread
                }

                record.startRecording()
                Log.i(TAG, "Mic capture running, sending to $serverIp:$SERVER_RECV_PORT")

                val socket = DatagramSocket()
                sendSocket = socket
                val serverAddr = InetAddress.getByName(serverIp)

                val shortBuf = ShortArray(SAMPLES_PER_PACKET)
                val pktBuf = ByteArray(UDP_HEADER_SIZE + BYTES_PER_PACKET)
                var seq = 0

                while (running && !Thread.currentThread().isInterrupted) {
                    val read = record.read(shortBuf, 0, SAMPLES_PER_PACKET)
                    if (read <= 0) continue

                    // Write seq header (big-endian)
                    pktBuf[0] = (seq shr 24).toByte()
                    pktBuf[1] = (seq shr 16).toByte()
                    pktBuf[2] = (seq shr 8).toByte()
                    pktBuf[3] = seq.toByte()

                    // Convert shorts to little-endian bytes
                    for (i in 0 until read) {
                        val s = shortBuf[i]
                        val off = UDP_HEADER_SIZE + i * 2
                        pktBuf[off] = (s.toInt() and 0xFF).toByte()
                        pktBuf[off + 1] = (s.toInt() shr 8 and 0xFF).toByte()
                    }

                    val len = UDP_HEADER_SIZE + read * 2
                    val packet = DatagramPacket(pktBuf, len, serverAddr, SERVER_RECV_PORT)
                    try {
                        socket.send(packet)
                    } catch (e: Exception) {
                        if (running) Log.w(TAG, "UDP send error: ${e.message}")
                    }
                    seq++

                    if (seq % 1000 == 0) {
                        Log.d(TAG, "Mic sent: $seq packets")
                    }
                }

                record.stop()
                record.release()
                socket.close()
                Log.i(TAG, "Mic capture ended, $seq packets sent")

            } catch (e: Exception) {
                Log.e(TAG, "Mic capture error: ${e.message}", e)
            }
        }.apply {
            isDaemon = true
            name = "mic-udp-send"
            start()
        }
    }

    // ── Speaker playback ← UDP from server ──

    private fun startSpeakerPlayback() {
        speakerThread = Thread {
            try {
                val socket = DatagramSocket(LISTEN_PORT)
                socket.soTimeout = 5000
                recvSocket = socket
                Log.i(TAG, "Listening for intercom audio on UDP port $LISTEN_PORT")

                val minBuf = AudioTrack.getMinBufferSize(
                    SAMPLE_RATE,
                    AudioFormat.CHANNEL_OUT_MONO,
                    AudioFormat.ENCODING_PCM_16BIT
                )
                val bufSize = maxOf(minBuf * 2, SAMPLE_RATE * 2 / 10)

                val track = AudioTrack.Builder()
                    .setAudioAttributes(
                        AudioAttributes.Builder()
                            .setUsage(AudioAttributes.USAGE_MEDIA)
                            .setContentType(AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build()
                    )
                    .setAudioFormat(
                        AudioFormat.Builder()
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setSampleRate(SAMPLE_RATE)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build()
                    )
                    .setBufferSizeInBytes(bufSize)
                    .setTransferMode(AudioTrack.MODE_STREAM)
                    .setPerformanceMode(AudioTrack.PERFORMANCE_MODE_LOW_LATENCY)
                    .build()
                audioTrack = track
                track.play()

                // Force max volume
                val am = getSystemService(AUDIO_SERVICE) as android.media.AudioManager
                am.setStreamVolume(
                    android.media.AudioManager.STREAM_MUSIC,
                    am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC),
                    0
                )

                Log.i(TAG, "Speaker playback ready")

                val buf = ByteArray(2048)
                val packet = DatagramPacket(buf, buf.size)
                var count = 0L

                while (running && !Thread.currentThread().isInterrupted) {
                    try {
                        socket.receive(packet)
                    } catch (_: java.net.SocketTimeoutException) {
                        continue // just keep waiting
                    }

                    if (packet.length > UDP_HEADER_SIZE) {
                        val pcmLen = packet.length - UDP_HEADER_SIZE
                        track.write(packet.data, UDP_HEADER_SIZE, pcmLen)
                        count++
                        if (count % 500 == 0L) {
                            Log.d(TAG, "Speaker: $count intercom packets played")
                        }
                    }
                }

                track.stop()
                track.release()
                socket.close()
                Log.i(TAG, "Speaker playback ended, $count packets")

            } catch (e: Exception) {
                Log.e(TAG, "Speaker playback error: ${e.message}", e)
            }
        }.apply {
            isDaemon = true
            name = "speaker-udp-recv"
            start()
        }
    }

    // ── Notification ──

    private fun createNotificationChannel() {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Audio Streaming",
            NotificationManager.IMPORTANCE_LOW
        ).apply {
            description = "Doorbell audio streaming"
        }
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val pi = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle("Doorbell Audio")
            .setContentText("Mic + intercom active")
            .setSmallIcon(R.drawable.ic_doorbell)
            .setContentIntent(pi)
            .setOngoing(true)
            .build()
    }
}
