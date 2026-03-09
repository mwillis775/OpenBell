#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# kiosk-setup.sh — One-time setup to lock down an Android phone as a
# dedicated doorbell appliance.  Run from a machine with ADB + USB connected
# to the phone.
#
# What this does:
#   1. Sets the OpenBell app as "device owner" (enables lock-task / kiosk mode)
#   2. Disables Google Play Store & system updater to prevent any OTA updates
#   3. Disables the setup wizard and package installer
#   4. Turns off auto-update in the Play Store
#   5. Keeps the phone awake and on Wi-Fi while plugged in
#
# Prerequisites:
#   - ADB installed on this machine
#   - USB debugging enabled on the phone
#   - The phone must have NO Google accounts signed in (required by Android
#     to set device owner). Remove all accounts first:
#       Settings → Accounts → remove every account
#   - The OpenBell APK must already be installed on the phone
#
# To UNDO kiosk mode later:
#   adb shell dpm remove-active-admin com.doorbell.app/.receiver.DoorbellDeviceAdmin
#   (then re-enable the packages below with: adb shell pm enable <package>)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_ID="com.doorbell.app"
ADMIN_RECEIVER="${APP_ID}/.receiver.DoorbellDeviceAdmin"

echo "────────────────────────────────────────────"
echo "  OpenBell Kiosk Mode Setup"
echo "────────────────────────────────────────────"
echo ""

# Check ADB
if ! command -v adb &>/dev/null; then
    echo "ERROR: adb not found. Install Android platform-tools first."
    exit 1
fi

# Wait for device
echo "→ Waiting for device..."
adb wait-for-device

DEVICE=$(adb get-serialno)
echo "  Connected: ${DEVICE}"
echo ""

# 1) Set device owner ─────────────────────────────────────────────────────────
echo "→ Setting ${APP_ID} as device owner..."
echo "  (If this fails, remove all Google accounts from the phone first)"
adb shell dpm set-device-owner "${ADMIN_RECEIVER}" || {
    echo ""
    echo "  FAILED — common fixes:"
    echo "    • Remove all accounts: Settings → Accounts → remove each one"
    echo "    • Make sure the app is installed: adb install -r app.apk"
    echo "    • Only one device owner can exist at a time"
    exit 1
}
echo "  ✓ Device owner set"
echo ""

# 2) Disable system updaters ──────────────────────────────────────────────────
echo "→ Disabling OTA / system updates..."
UPDATER_PACKAGES=(
    "com.android.vending"              # Google Play Store
    "com.google.android.apps.work.opc" # Google device-setup / zero-touch
)
# Vendor-specific OTA updater package names vary; try the common ones
OTA_PACKAGES=(
    "com.google.android.gms.update"
    "com.android.updater"
    "com.samsung.android.fota.agent"
    "com.samsung.android.scloud"
    "com.huawei.android.hwouc"
    "com.oneplus.opbackup"
    "com.google.android.apps.work.clouddpc"
)

for pkg in "${UPDATER_PACKAGES[@]}" "${OTA_PACKAGES[@]}"; do
    adb shell pm disable-user --user 0 "$pkg" 2>/dev/null && \
        echo "  ✓ Disabled: $pkg" || true
done
echo ""

# 3) Disable package installer & setup wizard ─────────────────────────────────
echo "→ Disabling package installer & setup wizard..."
INSTALLER_PACKAGES=(
    "com.google.android.packageinstaller"
    "com.android.packageinstaller"
    "com.google.android.setupwizard"
    "com.android.provision"
)
for pkg in "${INSTALLER_PACKAGES[@]}"; do
    adb shell pm disable-user --user 0 "$pkg" 2>/dev/null && \
        echo "  ✓ Disabled: $pkg" || true
done
echo ""

# 4) Keep screen on while charging & stay on Wi-Fi ────────────────────────────
echo "→ Configuring always-on display & Wi-Fi..."
adb shell settings put global stay_on_while_plugged_in 3   # AC + USB + wireless
adb shell settings put global wifi_sleep_policy 2          # never sleep
echo "  ✓ Done"
echo ""

# 5) Disable notification dots and heads-up notifications ─────────────────────
echo "→ Suppressing notifications..."
adb shell settings put secure notification_badging 0 2>/dev/null || true
echo "  ✓ Done"
echo ""

# 6) Reboot to apply everything cleanly ───────────────────────────────────────
echo "→ Rebooting phone..."
adb reboot

echo ""
echo "════════════════════════════════════════════"
echo "  Setup complete!"
echo ""
echo "  The phone will reboot, auto-launch OpenBell,"
echo "  and enter kiosk mode. It will not receive"
echo "  system updates or allow installing other apps."
echo ""
echo "  To undo later:"
echo "    adb shell dpm remove-active-admin ${ADMIN_RECEIVER}"
echo "════════════════════════════════════════════"
