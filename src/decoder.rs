//! Symphonia-based ALAC decoder wrapper.
//!
//! Decodes packet-by-packet and records the actual number of frames produced
//! per packet plus the planar PCM (i32, matching ALAC's native output).

use std::fs::File;
use std::path::Path;

use symphonia::core::audio::AudioBufferRef;
use symphonia::core::audio::Signal;
use symphonia::core::codecs::{Decoder, DecoderOptions};
use symphonia::core::formats::FormatOptions;
use symphonia::core::io::MediaSourceStream;
use symphonia::core::meta::MetadataOptions;
use symphonia::core::probe::Hint;

use crate::{bail, Result};

#[derive(Debug, Clone)]
pub struct DecodedPacket {
    pub packet_index: usize,
    pub track_ts: u64,
    pub duration_ts: u64,
    pub decoded_frames: usize,
}

#[derive(Debug, Clone)]
pub struct DecodeOutput {
    pub sample_rate: u32,
    pub channels: usize,
    /// Native ALAC bit depth from the magic cookie.
    pub bit_depth: u8,
    /// Planar decoded samples: [channel][frame].
    /// Values are normalized back to the native bit-depth domain (Symphonia
    /// returns them left-shifted into the 32-bit domain).
    pub planar: Vec<Vec<i32>>,
    pub packets: Vec<DecodedPacket>,
}

fn collect_frames(buf: AudioBufferRef<'_>, channels: usize) -> Result<Vec<Vec<i32>>> {
    let frames = buf.frames();
    let mut out = vec![Vec::with_capacity(frames); channels];
    match buf {
        AudioBufferRef::S32(buffer) => {
            let buffer = buffer.as_ref();
            for ch in 0..channels {
                out[ch].extend_from_slice(buffer.chan(ch));
            }
        }
        AudioBufferRef::S16(buffer) => {
            let buffer = buffer.as_ref();
            for ch in 0..channels {
                out[ch].extend(buffer.chan(ch).iter().map(|s| i32::from(*s)));
            }
        }
        AudioBufferRef::S24(buffer) => {
            let buffer = buffer.as_ref();
            for ch in 0..channels {
                out[ch].extend(buffer.chan(ch).iter().map(|s| s.0));
            }
        }
        other => bail!("unexpected ALAC sample buffer type: {}", other.spec().channels.count()),
    }
    Ok(out)
}

pub fn decode_file(path: &Path, expected_bit_depth: u8) -> Result<DecodeOutput> {
    let file = File::open(path)?;
    let mss = MediaSourceStream::new(Box::new(file), Default::default());
    let mut hint = Hint::new();
    hint.with_extension("m4a");

    let probe = symphonia::default::get_probe();
    let mut format = probe
        .format(
            &hint,
            mss,
            &FormatOptions::default(),
            &MetadataOptions::default(),
        )?
        .format;

    let track = format
        .default_track()
        .ok_or_else(|| crate::HarnessError("no default track".to_string()))?;
    let track_id = track.id;
    let codec_params = track.codec_params.clone();
    let sample_rate = codec_params
        .sample_rate
        .ok_or_else(|| crate::HarnessError("track missing sample rate".to_string()))?;
    let mut decoder: Box<dyn Decoder> =
        symphonia::default::get_codecs().make(&codec_params, &DecoderOptions::default())?;

    let mut planar: Vec<Vec<i32>> = Vec::new();
    let mut channels: usize = 0;
    let mut packets = Vec::new();
    let mut index = 0usize;

    loop {
        match format.next_packet() {
            Ok(packet) => {
                if packet.track_id() != track_id {
                    continue;
                }
                let decoded = decoder.decode(&packet)?;
                let decoded_channels = decoded.spec().channels.count();
                if channels == 0 {
                    channels = decoded_channels;
                    planar = vec![Vec::new(); channels];
                } else if decoded_channels != channels {
                    bail!(
                        "packet {index}: channel count changed to {decoded_channels} (expected {channels})"
                    );
                }
                let frames = decoded.frames();
                let chunk = collect_frames(decoded, channels)?;
                // Symphonia stores ALAC samples in the 32-bit domain; shift
                // back to the native bit depth before value comparison.
                let shift = 32 - expected_bit_depth;
                for (ch, samples) in chunk.into_iter().enumerate() {
                    planar[ch].extend(samples.into_iter().map(|s| s >> shift));
                }
                packets.push(DecodedPacket {
                    packet_index: index,
                    track_ts: packet.ts,
                    duration_ts: packet.dur,
                    decoded_frames: frames,
                });
                index += 1;
            }
            Err(symphonia::core::errors::Error::IoError(io_err))
                if io_err.kind() == std::io::ErrorKind::UnexpectedEof =>
            {
                break;
            }
            Err(symphonia::core::errors::Error::ResetRequired) => break,
            Err(err) => return Err(err.into()),
        }
    }

    Ok(DecodeOutput { sample_rate, channels, bit_depth: expected_bit_depth, planar, packets })
}
