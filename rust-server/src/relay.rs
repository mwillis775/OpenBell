/*!
Physical doorbell relay trigger.

When the phone's doorbell button is pressed, this module fires a WiFi relay
(Shelly 1, ESP8266, etc.) that momentarily closes the house's existing
doorbell circuit, ringing the real chime.

## Supported relay types

1. **Shelly 1 / Shelly 1 Mini** (recommended, ~$12)
   - HTTP API: `http://<ip>/relay/0?turn=on&timer=1`
   - Auto-turns off after `timer` seconds

2. **ESP8266/ESP32 + relay module** (DIY, ~$5)
   - Flash with simple firmware that accepts `GET /pulse?ms=800`
   - Or use Tasmota/ESPHome firmware

3. **Generic HTTP relay**
   - Any device that accepts an HTTP GET/POST to toggle

## Wiring

```
                    ┌─────────────┐
  Transformer 16V ──┤             ├── Doorbell Chime
        AC          │  Relay N.O. │
  (from existing ───┤  contacts   ├── (to existing
   doorbell wire)   └─────────────┘    doorbell wire)
                          │
                     WiFi relay
                     module (Shelly/ESP)
                          │
                       120V/USB
                       power
```

The relay's **Normally Open (N.O.)** contacts go in parallel with (or replace)
the existing doorbell push-button. When the relay fires for ~800ms, it closes
the circuit and the physical chime rings.

## Configuration

Set the relay URL via environment variable:
```
DOORBELL_RELAY_URL=http://192.168.0.50/relay/0?turn=on&timer=1
```

Or for a Tasmota device:
```
DOORBELL_RELAY_URL=http://192.168.0.50/cm?cmnd=Power%20On
```
*/

use std::time::Duration;
use tracing::{error, info, warn};

/// Default pulse duration if the relay URL doesn't include a timer param
const RELAY_PULSE_MS: u64 = 800;

/// Environment variable for the relay URL
const RELAY_URL_ENV: &str = "DOORBELL_RELAY_URL";

/// Trigger the physical doorbell relay.
/// Spawns a background task so it never blocks the WebSocket handler.
pub fn trigger_physical_doorbell() {
    let url = match std::env::var(RELAY_URL_ENV) {
        Ok(u) if !u.is_empty() => u,
        _ => {
            // No relay configured — that's fine, just skip
            info!("No physical relay configured (set {} to enable)", RELAY_URL_ENV);
            return;
        }
    };

    // Fire-and-forget background task
    tokio::spawn(async move {
        fire_relay(&url).await;
    });
}

/// Send the HTTP request to the relay device.
async fn fire_relay(url: &str) {
    info!("Triggering physical doorbell relay: {}", url);

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()
        .unwrap_or_default();

    match client.get(url).send().await {
        Ok(resp) => {
            let status = resp.status();
            if status.is_success() {
                info!("Relay triggered successfully (HTTP {})", status);
            } else {
                let body = resp.text().await.unwrap_or_default();
                warn!("Relay returned HTTP {}: {}", status, body);
            }
        }
        Err(e) => {
            error!("Failed to reach relay at {}: {}", url, e);
        }
    }

    // For relays that don't have a built-in timer (non-Shelly),
    // send an OFF request after the pulse duration
    if url.contains("timer=") {
        // Shelly handles auto-off via timer param — nothing to do
        return;
    }

    // Try to construct an "off" URL for common relay types
    if let Some(off_url) = derive_off_url(url) {
        tokio::time::sleep(Duration::from_millis(RELAY_PULSE_MS)).await;
        info!("Sending relay OFF: {}", off_url);
        match client.get(&off_url).send().await {
            Ok(resp) => {
                info!("Relay OFF response: HTTP {}", resp.status());
            }
            Err(e) => {
                warn!("Relay OFF request failed: {}", e);
            }
        }
    }
}

/// Try to derive an "off" URL from the "on" URL for common relay firmwares.
fn derive_off_url(on_url: &str) -> Option<String> {
    // Shelly: turn=on → turn=off
    if on_url.contains("turn=on") {
        return Some(on_url.replace("turn=on", "turn=off"));
    }
    // Tasmota: Power%20On → Power%20Off
    if on_url.contains("Power%20On") {
        return Some(on_url.replace("Power%20On", "Power%20Off"));
    }
    if on_url.contains("Power+On") {
        return Some(on_url.replace("Power+On", "Power+Off"));
    }
    // ESPHome / generic: /on → /off
    if on_url.ends_with("/on") {
        return Some(format!("{}off", &on_url[..on_url.len() - 2]));
    }
    // Can't figure it out — user's relay must handle its own timeout
    None
}
