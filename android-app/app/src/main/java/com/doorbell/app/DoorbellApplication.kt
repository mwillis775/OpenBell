package com.doorbell.app

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.os.Build

class DoorbellApplication : Application() {

    companion object {
        const val CHANNEL_STREAM = "camera_stream"
        const val CHANNEL_ALERTS = "doorbell_alerts"
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannels()
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val streamChannel = NotificationChannel(
                CHANNEL_STREAM,
                "Camera Stream",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Ongoing notification while camera is streaming"
            }

            val alertChannel = NotificationChannel(
                CHANNEL_ALERTS,
                "Doorbell Alerts",
                NotificationManager.IMPORTANCE_HIGH
            ).apply {
                description = "Alerts from doorbell events"
            }

            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(streamChannel)
            manager.createNotificationChannel(alertChannel)
        }
    }
}
