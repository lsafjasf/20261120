//! Synthetic PCM generation and ALAC sample-file production via ffmpeg.
//!
//! All generated PCM is interleaved little-endian (`s16le`/`s24le`/`s32le`),
//! which is the on-disk ordering ffmpeg consumes. The generator also returns
//! the planar view (per-channel samples) so decoder output can be compared in
//! both interleaved and planar layouts.

use std::fs::File;
use std::io::Write;
use std::path::Path;
use std::process::Command;

use crate::{bail, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BitDepth {
    B16 = 16,
    B24 = 24,
    B32 = 32,
}

impl BitDepth {
    pub fn bytes_per_sample(self) -> usize {
        match self {
            BitDepth::B16 => 2,
            BitDepth::B24 => 3,
            BitDepth::B32 => 4,
        }
    }

    pub fn max_value(self) -> i32 {
        match self {
            BitDepth::B16 => i16::MAX as i32,
            BitDepth::B24 => (1 << 23) - 1,
            BitDepth::B32 => i32::MAX,
        }
    }

    pub fn min_value(self) -> i32 {
        match self {
            BitDepth::B16 => i16::MIN as i32,
            BitDepth::B24 => -(1 << 23),
            BitDepth::B32 => i32::MIN,
        }
    }

    pub fn pcm_format(self) -> &'static str {
        match self {
            BitDepth::B16 => "s16le",
            BitDepth::B24 => "s24le",
            BitDepth::B32 => "s32le",
        }
    }

    pub fn label(self) -> &'static str {
        match self {
            BitDepth::B16 => "16bit",
            BitDepth::B24 => "24bit",
            BitDepth::B32 => "32bit",
        }
    }

    /// Effective usable bit depth after ALAC storage (32-bit PCM is stored at 24).
    pub fn stored_depth(self) -> BitDepth {
        match self {
            BitDepth::B32 => BitDepth::B24,
            other => other,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SignalKind {
    /// Deterministic multi-tone sine + linear ramp, distinct phase per channel.
    Tones,
    /// All samples zero.
    Zero,
    /// Alternating min/max rails; channel 1 stays at positive rail.
    FullScale,
    /// Tones with one sample's lowest bit flipped (bit-exact vs acoustic demo).
    Perturbed,
}

impl SignalKind {
    pub fn label(self) -> &'static str {
        match self {
            SignalKind::Tones => "tones",
            SignalKind::Zero => "zero",
            SignalKind::FullScale => "fullscale",
            SignalKind::Perturbed => "perturbed",
        }
    }
}

#[derive(Debug, Clone)]
pub struct PcmSpec {
    pub sample_rate: u32,
    pub channels: u8,
    pub depth: BitDepth,
    pub total_samples: usize,
    pub kind: SignalKind,
}

#[derive(Debug, Clone)]
pub struct GeneratedPcm {
    pub spec: PcmSpec,
    /// Interleaved little-endian bytes handed to the encoder.
    pub interleaved_bytes: Vec<u8>,
    /// Canonical sample values, planar: [channel][frame].
    pub planar: Vec<Vec<i32>>,
}

fn raw_sample(depth: BitDepth, value: i32) -> i32 {
    match depth {
        BitDepth::B16 => (value as i16) as i32,
        BitDepth::B24 => {
            let masked = value & 0x00FF_FFFF;
            if masked & 0x0080_0000 != 0 {
                masked - 0x0100_0000
            } else {
                masked
            }
        }
        BitDepth::B32 => value,
    }
}

pub fn generate(spec: PcmSpec) -> GeneratedPcm {
    let ch = spec.channels as usize;
    let n = spec.total_samples;
    let effective = spec.depth.stored_depth();
    let max = effective.max_value();
    let min = effective.min_value();

    let mut planar = vec![vec![0i32; n]; ch];
    for frame in 0..n {
        for (channel, buf) in planar.iter_mut().enumerate() {
            let value = match spec.kind {
                SignalKind::Zero => 0,
                SignalKind::FullScale => {
                    if frame % 2 == 0 {
                        max
                    } else {
                        // Alternate rails; later channels invert the phase.
                        if channel % 2 == 0 {
                            min
                        } else {
                            max
                        }
                    }
                }
                SignalKind::Tones | SignalKind::Perturbed => {
                    let t = frame as f64 / spec.sample_rate as f64;
                    let phase = (channel as f64) * std::f64::consts::FRAC_PI_4;
                    let tone1 = (2.0 * std::f64::consts::PI * 440.0 * t + phase).sin();
                    let tone2 = 0.5 * (2.0 * std::f64::consts::PI * 1234.5 * t + phase).sin();
                    let ramp = 0.25 * ((frame % 512) as f64 / 512.0 - 0.5);
                    let normalized = (tone1 + tone2) * 0.45 + ramp;
                    (normalized * f64::from(max)).round() as i32
                }
            };
            buf[frame] = raw_sample(effective, value);
        }
    }

    if spec.kind == SignalKind::Perturbed {
        // Flip the least significant bit of one mid-stream sample on channel 0.
        planar[0][n / 2] ^= 1;
    }

    let bps = spec.depth.bytes_per_sample();
    let container_shift = match spec.depth {
        BitDepth::B32 => 8,
        _ => 0,
    };
    let mut bytes = Vec::with_capacity(n * ch * bps);
    let mut write_sample = |value: i32| match spec.depth {
        BitDepth::B16 => bytes.extend_from_slice(&(value as i16).to_le_bytes()),
        BitDepth::B24 => {
            let bits = (value as u32) & 0x00FF_FFFF;
            bytes.extend_from_slice(&[bits as u8, (bits >> 8) as u8, (bits >> 16) as u8]);
        }
        BitDepth::B32 => bytes.extend_from_slice(&(value << container_shift).to_le_bytes()),
    };
    for frame in 0..n {
        for channel in 0..ch {
            // Frame values into the requested container width. For a 32-bit
            // container holding 24-bit-meaningful data, shift up by 8 bits so
            // the on-disk PCM matches the encoder's 32-bit input convention;
            // the comparison side applies the inverse normalization.
            write_sample(planar[channel][frame]);
        }
    }

    GeneratedPcm { spec, interleaved_bytes: bytes, planar }
}

fn ffmpeg_channel_layout(channels: u8) -> Option<&'static str> {
    match channels {
        1 => Some("mono"),
        2 => Some("stereo"),
        3 => Some("3.0"),
        4 => Some("4.0"),
        5 => Some("5.0"),
        6 => Some("5.1"),
        8 => Some("7.1"),
        _ => None,
    }
}

/// Encode interleaved PCM into an ALAC-in-MP4 (.m4a) file with ffmpeg.
pub fn encode_alac(ffmpeg: &Path, pcm: &GeneratedPcm, out_path: &Path) -> Result<()> {
    let pcm_path = out_path.with_extension("pcm");
    {
        let mut f = File::create(&pcm_path)?;
        f.write_all(&pcm.interleaved_bytes)?;
    }

    let mut cmd = Command::new(ffmpeg);
    cmd.args(["-hide_banner", "-loglevel", "error", "-y", "-f", pcm.spec.depth.pcm_format()])
        .args(["-ar", &pcm.spec.sample_rate.to_string(), "-ac", &pcm.spec.channels.to_string()])
        .arg("-i")
        .arg(&pcm_path);
    if let Some(layout) = ffmpeg_channel_layout(pcm.spec.channels) {
        if pcm.spec.channels > 2 {
            cmd.args(["-channel_layout", layout]);
        }
    } else {
        bail!("no ffmpeg channel layout for {} channels", pcm.spec.channels);
    }
    cmd.args(["-c:a", "alac"]).arg(out_path);

    let output = cmd.output()?;
    if !output.status.success() {
        bail!(
            "ffmpeg alac encode failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    Ok(())
}
