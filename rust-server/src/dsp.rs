/*!
Audio DSP primitives for noise reduction and feedback suppression.

- **NoiseGate** — eliminates low-level static / hiss when nobody is speaking.
- **HighPassFilter** — removes DC offset and sub-80 Hz rumble.
- **Ducker** — attenuates the PC mic when phone audio is playing through
  the speakers, preventing acoustic feedback loops.
*/

use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

/// Audio pipeline sample rate.
const SAMPLE_RATE: f32 = 48_000.0;

// ─── Noise Gate ──────────────────────────────────────────────────────────────

/// A noise gate with hysteresis and per-sample envelope smoothing.
///
/// When the RMS of a chunk is below the threshold the gate smoothly
/// closes, driving the signal toward silence.  A small hysteresis band
/// prevents rapid open/close chatter on borderline signals.
pub struct NoiseGate {
    open_threshold: f32,
    close_threshold: f32,
    envelope: f32,
    attack_coeff: f32,
    release_coeff: f32,
}

impl NoiseGate {
    /// Create a new noise gate.
    ///
    /// * `threshold_db`  — RMS level (dBFS) to open the gate (e.g. −40)
    /// * `hysteresis_db` — how many dB *below* the threshold to close (e.g. 6)
    /// * `attack_ms`     — milliseconds to fully open   (fast → 1–2 ms)
    /// * `release_ms`    — milliseconds to fully close   (slow → 50–80 ms)
    pub fn new(threshold_db: f32, hysteresis_db: f32, attack_ms: f32, release_ms: f32) -> Self {
        Self {
            open_threshold: db_to_linear(threshold_db),
            close_threshold: db_to_linear(threshold_db - hysteresis_db),
            envelope: 0.0,
            attack_coeff: time_constant(attack_ms),
            release_coeff: time_constant(release_ms),
        }
    }

    /// Process a buffer of `i16` PCM samples **in-place**.
    pub fn process(&mut self, samples: &mut [i16]) {
        if samples.is_empty() {
            return;
        }

        let rms = compute_rms_i16(samples);

        let target = if rms > self.open_threshold {
            1.0
        } else if rms < self.close_threshold {
            0.0
        } else {
            // Inside hysteresis band — hold current state
            self.envelope
        };

        let coeff = if target > self.envelope {
            self.attack_coeff
        } else {
            self.release_coeff
        };

        for s in samples.iter_mut() {
            self.envelope += coeff * (target - self.envelope);
            *s = (*s as f32 * self.envelope) as i16;
        }
    }
}

// ─── High-Pass Filter ────────────────────────────────────────────────────────

/// Single-pole high-pass filter.
///
/// Removes DC offset and very low-frequency rumble below `cutoff_hz`.
/// Transfer function:  y[n] = α·(y[n-1] + x[n] − x[n-1])
pub struct HighPassFilter {
    alpha: f32,
    prev_in: f32,
    prev_out: f32,
}

impl HighPassFilter {
    pub fn new(cutoff_hz: f32) -> Self {
        let rc = 1.0 / (2.0 * std::f32::consts::PI * cutoff_hz);
        let dt = 1.0 / SAMPLE_RATE;
        Self {
            alpha: rc / (rc + dt),
            prev_in: 0.0,
            prev_out: 0.0,
        }
    }

    /// Process a buffer of `i16` PCM samples **in-place**.
    pub fn process(&mut self, samples: &mut [i16]) {
        for s in samples.iter_mut() {
            let x = *s as f32;
            let y = self.alpha * (self.prev_out + x - self.prev_in);
            self.prev_in = x;
            self.prev_out = y;
            *s = y.clamp(-32768.0, 32767.0) as i16;
        }
    }
}

// ─── Ducker (feedback suppression) ───────────────────────────────────────────

/// Attenuates the PC mic signal when the far-end (phone) audio is playing
/// through the PC speakers, preventing the acoustic feedback loop:
///
///   phone → speakers → mic → phone → speakers → …
///
/// Works by reading a shared atomic that carries the current speaker-output
/// RMS level.  When that level exceeds a threshold the mic gain is driven
/// toward `floor_gain` (heavy attenuation).
pub struct Ducker {
    speaker_rms: Arc<AtomicU32>,
    threshold: f32,
    floor_gain: f32,
    envelope: f32,
    attack_coeff: f32,
    release_coeff: f32,
}

impl Ducker {
    /// Create a new ducker.
    ///
    /// * `speaker_rms`  — shared atomic carrying the current speaker RMS
    ///                     (stored as `f32::to_bits`)
    /// * `threshold_db` — speaker RMS above this triggers ducking (e.g. −42)
    /// * `floor_db`     — maximum attenuation when fully ducked (e.g. −30)
    /// * `attack_ms`    — how fast ducking engages     (fast → 2–5 ms)
    /// * `release_ms`   — how slowly ducking releases  (slow → 250–400 ms)
    pub fn new(
        speaker_rms: Arc<AtomicU32>,
        threshold_db: f32,
        floor_db: f32,
        attack_ms: f32,
        release_ms: f32,
    ) -> Self {
        Self {
            speaker_rms,
            threshold: db_to_linear(threshold_db),
            floor_gain: db_to_linear(floor_db),
            envelope: 1.0,
            attack_coeff: time_constant(attack_ms),
            release_coeff: time_constant(release_ms),
        }
    }

    /// Process mic samples: attenuate when the speaker is active.
    pub fn process(&mut self, samples: &mut [i16]) {
        let speaker_level = f32::from_bits(self.speaker_rms.load(Ordering::Relaxed));

        let target = if speaker_level > self.threshold {
            self.floor_gain
        } else {
            1.0
        };

        let coeff = if target < self.envelope {
            self.attack_coeff // duck fast
        } else {
            self.release_coeff // un-duck slowly
        };

        for s in samples.iter_mut() {
            self.envelope += coeff * (target - self.envelope);
            *s = (*s as f32 * self.envelope) as i16;
        }
    }
}

// ─── Shared RMS reporter ────────────────────────────────────────────────────

/// Update the shared atomic with the current RMS of speaker-output samples.
///
/// Called from the phone→speaker pipeline so the mic→phone pipeline can
/// read the level and duck accordingly.
pub fn update_speaker_rms(rms_atom: &AtomicU32, samples: &[i16]) {
    let rms = compute_rms_i16(samples);
    rms_atom.store(rms.to_bits(), Ordering::Relaxed);
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

/// Convert decibels (dBFS) to linear amplitude.
fn db_to_linear(db: f32) -> f32 {
    10.0_f32.powf(db / 20.0)
}

/// Compute a per-sample smoothing coefficient from a time constant in ms.
///
/// `coeff = 1 − exp(−1 / (ms × sample_rate / 1000))`
fn time_constant(ms: f32) -> f32 {
    if ms <= 0.0 {
        return 1.0;
    }
    1.0 - (-1.0 / (ms * SAMPLE_RATE / 1000.0)).exp()
}

/// RMS of an `i16` PCM buffer, returned in the normalised 0–1 range.
pub fn compute_rms_i16(samples: &[i16]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    let sum_sq: f64 = samples
        .iter()
        .map(|&s| {
            let f = s as f64 / 32768.0;
            f * f
        })
        .sum();
    (sum_sq / samples.len() as f64).sqrt() as f32
}
