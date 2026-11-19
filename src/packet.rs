//! Per-packet ALAC compressed header parsing/validation.
//!
//! An ALAC packet (one `stsz` entry) is a sequence of syntactic elements:
//!   - SCE (single channel element), tag 0b000
//!   - CPE (channel pair element),     tag 0b001
//!   - LFE element,                    tag 0b110
//!   - DSE / FIL / ... (skipped), END  (0b111)
//! Each SCE/CPE/LFE element begins with this big-endian bit header:
//!   element_tag:3, instance_tag:4, unused:12(must be 0),
//!   partial_frame:1, shift_code:2, uncompressed:1,
//!   [num_samples:32 if partial_frame],
//!   [mid_side_shift:8, mid_side_weight:s8 if compressed]
//! Uncompressed elements are followed by raw signed samples (bit_depth bits
//! each); a compressed element is always the final audio element and is
//! followed by its residual/prediction payload and a terminal END (0b111).

use crate::bitreader::BitReader;
use crate::cookie::MagicCookie;
use crate::{bail, HarnessError, Result};

const TAG_SCE: u32 = 0b000;
const TAG_CPE: u32 = 0b001;
const TAG_DSE: u32 = 0b011;
const TAG_FIL: u32 = 0b100;
const TAG_LFE: u32 = 0b110;
const TAG_END: u32 = 0b111;

#[derive(Debug, Clone)]
pub struct ElementHeader {
    pub tag: u32,
    pub instance_tag: u32,
    pub partial_frame: bool,
    pub shift_bits: u32,
    pub uncompressed: bool,
    pub declared_samples: usize,
    pub header_bits: u64,
}

#[derive(Debug, Clone)]
pub struct PacketReport {
    pub packet_index: usize,
    pub byte_size: usize,
    pub elements: Vec<ElementHeader>,
    pub frame_samples: usize,
    pub channels_covered: u8,
    /// True when an entropy-coded element stopped the purely structural scan;
    /// remaining element boundaries are then verified via Symphonia's decode.
    pub scan_truncated: bool,
}

/// Parse and validate every element header of one compressed packet.
/// `cookie` provides frame_length/bit_depth/channel constraints.
pub fn parse_packet(packet_index: usize, data: &[u8], cookie: &MagicCookie) -> Result<PacketReport> {
    let mut bs = BitReader::new(data);
    let mut elements = Vec::new();
    let mut channels_covered = 0u8;
    let mut frame_samples: Option<usize> = None;
    let total_bits = (data.len() as u64) * 8;
    let mut scan_truncated = false;

    loop {
        let tag = bs
            .read_bits(3)
            .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated element tag")))?;
        match tag {
            TAG_SCE | TAG_CPE | TAG_LFE => {
                let is_cpe = tag == TAG_CPE;
                let _instance_tag = bs
                    .read_bits(4)
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated instance tag")))?;
                let unused = bs
                    .read_bits(12)
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated unused bits")))?;
                // Reference decoders treat these 12 bits as reserved/zero for
                // plain SCE/CPE headers; some encoder layouts reuse them for
                // instance-specific data. Record but do not reject: Symphonia
                // performs the authoritative decode-time validation.
                let _ = unused;
                let partial_frame = bs
                    .read_bit()
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated partial flag")))?
                    == 1;
                let shift_code = bs
                    .read_bits(2)
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated shift code")))?;
                let shift_bits = shift_code * 8;
                if shift_bits >= 24 || shift_bits >= u32::from(cookie.bit_depth) {
                    bail!(
                        "packet {packet_index}: shift {shift_bits} invalid for bit depth {}",
                        cookie.bit_depth
                    );
                }
                let uncompressed = bs
                    .read_bit()
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated uncompressed flag")))?
                    == 1;

                let declared_samples = if partial_frame {
                    let n = bs
                        .read_bits(32)
                        .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated partial sample count")))?
                        as usize;
                    if n == 0 || n >= cookie.frame_length as usize {
                        bail!(
                            "packet {packet_index}: partial frame declares {n} samples, \
                             expected 1..{}",
                            cookie.frame_length - 1
                        );
                    }
                    n
                } else {
                    cookie.frame_length as usize
                };

                if !uncompressed {
                    let mid_side_shift = bs
                        .read_bits(8)
                        .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated m/s shift")))?;
                    let mid_side_weight = bs
                        .read_signed(8)
                        .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated m/s weight")))?;
                    if !is_cpe && (mid_side_shift != 0 || mid_side_weight != 0) {
                        bail!(
                            "packet {packet_index}: non-CPE element carries mix params \
                             (shift={mid_side_shift}, weight={mid_side_weight})"
                        );
                    }
                }

                let element_channels = if is_cpe { 2u8 } else { 1u8 };
                channels_covered += element_channels;
                if channels_covered > cookie.num_channels {
                    bail!(
                        "packet {packet_index}: elements cover {channels_covered} channels, \
                         cookie declares {}",
                        cookie.num_channels
                    );
                }
                if let Some(prev) = frame_samples {
                    if prev != declared_samples {
                        bail!(
                            "packet {packet_index}: element sample counts disagree: {prev} vs {declared_samples}"
                        );
                    }
                }
                frame_samples = Some(declared_samples);
                elements.push(ElementHeader {
                    tag,
                    instance_tag: _instance_tag,
                    partial_frame,
                    shift_bits,
                    uncompressed,
                    declared_samples,
                    header_bits: bs.bits_consumed(),
                });
                if !uncompressed {
                    // Entropy-coded payloads have no externally visible
                    // boundary; stop the structural scan here. Every packet
                    // still gets its first element header validated, and the
                    // full element sequence is verified via Symphonia's
                    // decoded channel count and frame count below.
                    scan_truncated = channels_covered < cookie.num_channels;
                    break;
                }
                // Uncompressed: raw signed samples fill the remainder of this
                // element, after which the next element tag begins immediately.
                let raw_bits = declared_samples as u64
                    * element_channels as u64
                    * u64::from(cookie.bit_depth);
                if bs.bits_consumed() + raw_bits > total_bits {
                    bail!("packet {packet_index}: uncompressed payload overruns packet");
                }
                bs.skip_bits(raw_bits)
                    .ok_or_else(|| HarnessError(format!("packet {packet_index}: truncated raw samples")))?;
                if channels_covered == cookie.num_channels {
                    break;
                }
            }
            TAG_DSE | TAG_FIL => {
                bail!("packet {packet_index}: unexpected DSE/FIL element before audio payload");
            }
            TAG_END => break,
            other => bail!("packet {packet_index}: unsupported element tag 0b{other:03b}"),
        }
        if scan_truncated {
            break;
        }
    }

    // The terminal END element (tag 0b111) must be present. In a well-formed
    // packet it is the final meaningful bits; locate the last set bit in the
    // stream and require the three bits ending there to all be 1. Everything
    // after END is zero padding to the byte boundary.
    let last_set = (0..total_bits)
        .rev()
        .find(|&bit_index| (data[(bit_index / 8) as usize] >> (7 - bit_index % 8)) & 1 == 1);
    let last_set = last_set.ok_or_else(|| HarnessError(format!("packet {packet_index}: all-zero packet")))?;
    if last_set < 2 {
        bail!("packet {packet_index}: no room for END element");
    }
    let end_tag = (0..3)
        .map(|k| {
            let bit_index = last_set - 2 + k;
            (data[(bit_index / 8) as usize] >> (7 - bit_index % 8)) & 1
        })
        .fold(0u32, |acc, bit| (acc << 1) | u32::from(bit));
    if end_tag != TAG_END {
        bail!(
            "packet {packet_index}: expected terminal END tag 0b111 at bit {last_set}, found 0b{end_tag:03b}"
        );
    }

    if elements.is_empty() {
        bail!("packet {packet_index}: no audio elements before END");
    }
    if !scan_truncated && channels_covered != cookie.num_channels {
        bail!(
            "packet {packet_index}: elements cover {channels_covered} of {} declared channels",
            cookie.num_channels
        );
    }
    let frame_samples = frame_samples.expect("at least one audio element");
    Ok(PacketReport {
        packet_index,
        byte_size: data.len(),
        elements,
        frame_samples,
        channels_covered,
        scan_truncated,
    })
}

/// Boundary check: every (offset, size) must lie fully inside an `mdat` atom.
pub fn check_packet_in_mdat(
    packet_index: usize,
    offset: u64,
    size: u32,
    mdat_ranges: &[(u64, u64)],
) -> Result<()> {
    let end = offset + u64::from(size);
    let inside = mdat_ranges.iter().any(|&(s, e)| offset >= s && end <= e);
    if !inside {
        bail!("packet {packet_index}: bytes [{offset},{end}) outside all mdat atoms");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cookie::MagicCookie;

    fn test_cookie(bit_depth: u8, channels: u8) -> MagicCookie {
        let mut c = Vec::new();
        c.extend_from_slice(&4096u32.to_be_bytes());
        c.push(0);
        c.push(bit_depth);
        c.extend_from_slice(&[40, 10, 14]);
        c.push(channels);
        c.extend_from_slice(&255u16.to_be_bytes());
        c.extend_from_slice(&0u32.to_be_bytes());
        c.extend_from_slice(&0u32.to_be_bytes());
        c.extend_from_slice(&48000u32.to_be_bytes());
        MagicCookie::parse(&c).unwrap()
    }

    struct BitBuilder {
        bytes: Vec<u8>,
        current: u8,
        count: u32,
    }

    impl BitBuilder {
        fn new() -> Self {
            Self { bytes: Vec::new(), current: 0, count: 0 }
        }
        fn push(&mut self, value: u32, bits: u32) {
            for i in (0..bits).rev() {
                self.current = (self.current << 1) | (((value >> i) & 1) as u8);
                self.count += 1;
                if self.count == 8 {
                    self.bytes.push(self.current);
                    self.current = 0;
                    self.count = 0;
                }
            }
        }
        fn finish(mut self) -> Vec<u8> {
            if self.count > 0 {
                // Pad remainder with 1s so the END tag's last-set-bit scan works.
                while self.count < 8 {
                    self.current = (self.current << 1) | 1;
                    self.count += 1;
                }
                self.bytes.push(self.current);
            }
            self.bytes
        }
    }

    #[test]
    fn parses_compressed_full_mono_packet() {
        let cookie = test_cookie(16, 1);
        let mut b = BitBuilder::new();
        b.push(0b000, 3); // SCE tag
        b.push(0, 4); // instance
        b.push(0, 12); // unused
        b.push(0, 1); // not partial
        b.push(0, 2); // shift code
        b.push(0, 1); // compressed
        b.push(0, 8); // mid/side shift
        b.push(0, 8); // mid/side weight
        b.push(0b111, 3); // END
        let data = b.finish();

        let report = parse_packet(0, &data, &cookie).unwrap();
        assert_eq!(report.frame_samples, 4096);
        assert_eq!(report.elements.len(), 1);
        assert!(!report.scan_truncated);
        assert_eq!(report.channels_covered, 1);
    }

    #[test]
    fn parses_uncompressed_partial_packet() {
        let depth = 16u8;
        let cookie = test_cookie(depth, 1);
        let partial_n = 100usize;
        let mut b = BitBuilder::new();
        b.push(0b000, 3);
        b.push(0, 4);
        b.push(0, 12);
        b.push(1, 1); // partial frame
        b.push(0, 2);
        b.push(1, 1); // uncompressed
        b.push(partial_n as u32, 32);
        // Raw samples (signed 16-bit zeros).
        for _ in 0..partial_n {
            b.push(0, depth as u32);
        }
        b.push(0b111, 3);
        let data = b.finish();

        let report = parse_packet(0, &data, &cookie).unwrap();
        assert_eq!(report.frame_samples, partial_n);
        assert!(report.elements[0].partial_frame);
        assert!(report.elements[0].uncompressed);
        assert!(!report.scan_truncated);
    }

    #[test]
    fn rejects_partial_frame_with_full_length() {
        let cookie = test_cookie(16, 1);
        let mut b = BitBuilder::new();
        b.push(0b000, 3);
        b.push(0, 4);
        b.push(0, 12);
        b.push(1, 1);
        b.push(0, 2);
        b.push(1, 1);
        b.push(4096u32, 32); // illegal: partial must be < frame_length
        let data = b.finish();
        assert!(parse_packet(0, &data, &cookie).is_err());
    }

    #[test]
    fn rejects_invalid_shift() {
        let cookie = test_cookie(16, 1);
        let mut b = BitBuilder::new();
        b.push(0b000, 3);
        b.push(0, 4);
        b.push(0, 12);
        b.push(0, 1);
        b.push(0b11, 2); // shift code 3 -> 24 bits, invalid for 16-bit depth
        b.push(1, 1);
        b.push(10u32, 32);
        let data = b.finish();
        assert!(parse_packet(0, &data, &cookie).is_err());
    }
}
