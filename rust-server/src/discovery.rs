use mdns_sd::{ServiceDaemon, ServiceInfo};
use tracing::{info, warn};

/// Advertise our doorbell server via mDNS so phones can discover it automatically
pub fn advertise(port: u16) {
    std::thread::spawn(move || {
        let mdns = match ServiceDaemon::new() {
            Ok(d) => d,
            Err(e) => {
                warn!("mDNS daemon failed to start: {}. Devices must connect manually.", e);
                return;
            }
        };

        let hostname = gethostname();
        let local_ip = get_local_ip().unwrap_or_else(|| "0.0.0.0".into());

        let service_type = "_doorbell._tcp.local.";
        let instance_name = "Doorbell Server";

        match ServiceInfo::new(
            service_type,
            instance_name,
            &format!("{}.local.", hostname),
            &local_ip,
            port,
            None,
        ) {
            Ok(service_info) => {
                if let Err(e) = mdns.register(service_info) {
                    warn!("mDNS registration failed: {}", e);
                } else {
                    info!("mDNS: advertising as {} on {}:{}", instance_name, local_ip, port);
                }
            }
            Err(e) => {
                warn!("mDNS service info error: {}", e);
            }
        }

        // Keep the daemon alive
        std::thread::park();
    });
}

fn gethostname() -> String {
    hostname::get()
        .map(|h| h.to_string_lossy().into_owned())
        .unwrap_or_else(|_| "doorbell-server".into())
}

fn get_local_ip() -> Option<String> {
    // Try to find the first non-loopback IPv4 address
    let socket = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    socket.connect("192.168.1.1:80").ok()?;
    socket.local_addr().ok().map(|a| a.ip().to_string())
}
