package com.doorbell.app.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.doorbell.app.ui.MainActivity

/**
 * Receives BOOT_COMPLETED but does NOT auto-launch the app.
 * The user opens OpenBell manually; kiosk mode activates once the app starts.
 */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        // intentionally empty — boot receiver kept for future use
    }
}
