package com.doorbell.app.receiver

import android.app.admin.DeviceAdminReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log

/**
 * Device admin receiver for kiosk / lock-task mode.
 *
 * When this app is set as device owner via ADB, it can pin itself to the
 * screen so the phone becomes a dedicated doorbell appliance — no status bar,
 * no notifications, no way to leave the app.
 */
class DoorbellDeviceAdmin : DeviceAdminReceiver() {

    companion object {
        private const val TAG = "DoorbellDeviceAdmin"

        fun getComponentName(context: Context): ComponentName =
            ComponentName(context.applicationContext, DoorbellDeviceAdmin::class.java)
    }

    override fun onEnabled(context: Context, intent: Intent) {
        super.onEnabled(context, intent)
        Log.i(TAG, "Device admin enabled")
    }

    override fun onDisabled(context: Context, intent: Intent) {
        super.onDisabled(context, intent)
        Log.i(TAG, "Device admin disabled")
    }

    override fun onLockTaskModeEntering(context: Context, intent: Intent, pkg: String) {
        super.onLockTaskModeEntering(context, intent, pkg)
        Log.i(TAG, "Entering lock task (kiosk) mode")
    }

    override fun onLockTaskModeExiting(context: Context, intent: Intent) {
        super.onLockTaskModeExiting(context, intent)
        Log.i(TAG, "Exiting lock task (kiosk) mode")
    }
}
