//! ALAC magic cookie ("ALACSpecificConfig") parser.
//!
//! Layout of the mandatory 24-byte core (all values big-endian):
//!   frame_length:u32, compatible_version:u8, bit_depth:u8, pb:u8, mb:u8,
//!   kb:u8, num_channels:u8, max_run:u16, max_frame_bytes:u32,
//!   avg_bit_rate:u32, sample_rate:u32
//! The optional 24-byte extension is a `chan` atom describing the channel
//! layout tag (CoreAudio channel layout identifiers).

use crate::{bail, Result};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChannelLayoutInfo {
    pub tag: u32,
    pub name: &'static str,
    pub channels: u8,
}

#[derive(Debug, Clone)]
pub struct MagicCookie {
    pub frame_length: u32,
    pub compatible_version: u8,
    pub bit_depth: u8,
    pub pb: u8,
    pub mb: u8,
    pub kb: u8,
    pub num_channels: u8,
    pub max_run: u16,
    pub max_frame_bytes: u32,
    pub avg_bit_rate: u32,
    pub sample_rate: u32,
    pub layout: Option<ChannelLayoutInfo>,
    pub raw_len: usize,
}

fn be_u16(b: &[u8]) -> u16 {
    u16::from_be_bytes([b[0], b[1]])
}

fn be_u32(b: &[u8]) -> u32 {
    u32::from_be_bytes([b[0], b[1], b[2], b[3]])
}

/// CoreAudio channel layout tags carried by the `chan` extension.
fn layout_for_tag(tag: u32) -> Option<ChannelLayoutInfo> {
    let (name, channels) = match tag {
        0x640001 => ("kAudioChannelLayoutTag_Mono", 1),
        0x650002 => ("kAudioChannelLayoutTag_Stereo", 2),
        0x710003 => ("MPEG_3_0_B", 3),
        0x740004 => ("MPEG_4_0_B", 4),
        0x780005 => ("MPEG_5_0_D", 5),
        0x7c0006 => ("MPEG_5_1_D", 6),
        0x8e0007 => ("AAC_6_1", 7),
        0x7f0008 => ("MPEG_7_1_B", 8),
        _ => return None,
    };
    Some(ChannelLayoutInfo { tag, name, channels })
}

impl MagicCookie {
    /// Parse the 24- or 48-byte cookie, validating every reserved/constraint
    /// field instead of trusting the encoder blindly.
    pub fn parse(cookie: &[u8]) -> Result<MagicCookie> {
        if cookie.len() != 24 && cookie.len() != 48 {
            bail!("invalid magic cookie size {} (expected 24 or 48)", cookie.len());
        }
        let c = cookie;
        let parsed = MagicCookie {
            frame_length: be_u32(&c[0..4]),
            compatible_version: c[4],
            bit_depth: c[5],
            pb: c[6],
            mb: c[7],
            kb: c[8],
            num_channels: c[9],
            max_run: be_u16(&c[10..12]),
            max_frame_bytes: be_u32(&c[12..16]),
            avg_bit_rate: be_u32(&c[16..20]),
            sample_rate: be_u32(&c[20..24]),
            layout: None,
            raw_len: cookie.len(),
        };

        if parsed.compatible_version != 0 {
            bail!("unsupported ALAC compatible version {}", parsed.compatible_version);
        }
        if !(1..=32).contains(&parsed.bit_depth) {
            bail!("invalid bit depth {} in cookie", parsed.bit_depth);
        }
        if !(1..=8).contains(&parsed.num_channels) {
            bail!("invalid channel count {} in cookie", parsed.num_channels);
        }
        if parsed.frame_length == 0 {
            bail!("frame_length 0 in cookie");
        }
        if parsed.sample_rate == 0 {
            bail!("sample_rate 0 in cookie");
        }
        // Real-world ALAC encoders use a power-of-two frame length; reject
        // implausible values rather than silently trusting the cookie.
        if !parsed.frame_length.is_power_of_two() || parsed.frame_length > 1 << 20 {
            bail!(
                "implausible frame_length {} in cookie (expected a power of two <= 1048576)",
                parsed.frame_length
            );
        }

        let mut parsed = parsed;
        if cookie.len() == 48 {
            let ext = &c[24..48];
            if be_u32(&ext[0..4]) != 24 {
                bail!("chan extension: invalid size field");
            }
            if &ext[4..8] != b"chan" {
                bail!("chan extension: missing 'chan' identifier");
            }
            if be_u32(&ext[8..12]) != 0 {
                bail!("chan extension: version must be 0");
            }
            let tag = be_u32(&ext[12..16]);
            let layout = layout_for_tag(tag)
                .ok_or_else(|| format!("unknown CoreAudio channel layout tag 0x{tag:08x}"))?;
            if layout.channels != parsed.num_channels {
                bail!(
                    "cookie channels {} disagree with layout {} ({})",
                    parsed.num_channels,
                    layout.channels,
                    layout.name
                );
            }
            if be_u32(&ext[16..20]) != 0 || be_u32(&ext[20..24]) != 0 {
                bail!("chan extension: reserved fields must be 0");
            }
            parsed.layout = Some(layout);
        }
        Ok(parsed)
    }

    /// Implicit layout when no `chan` extension is present.
    pub fn implicit_layout_name(&self) -> &'static str {
        match self.num_channels {
            1 => "implicit mono",
            2 => "implicit stereo (L,R)",
            _ => "implicit discrete multi-channel",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn core_cookie(bit_depth: u8, channels: u8) -> Vec<u8> {
        let mut c = Vec::new();
        c.extend_from_slice(&4096u32.to_be_bytes()); // frame_length
        c.push(0); // version
        c.push(bit_depth);
        c.extend_from_slice(&[40, 10, 14]); // pb, mb, kb
        c.push(channels);
        c.extend_from_slice(&255u16.to_be_bytes()); // max_run
        c.extend_from_slice(&0u32.to_be_bytes()); // max_frame_bytes
        c.extend_from_slice(&0u32.to_be_bytes()); // avg_bit_rate
        c.extend_from_slice(&48000u32.to_be_bytes()); // sample_rate
        assert_eq!(c.len(), 24);
        c
    }

    #[test]
    fn parses_24_byte_cookie_with_implicit_layout() {
        let cookie = MagicCookie::parse(&core_cookie(16, 2)).unwrap();
        assert_eq!(cookie.frame_length, 4096);
        assert_eq!(cookie.bit_depth, 16);
        assert_eq!(cookie.num_channels, 2);
        assert!(cookie.layout.is_none());
    }

    #[test]
    fn parses_48_byte_cookie_with_chan_extension() {
        let mut c = core_cookie(24, 6);
        c.extend_from_slice(&24u32.to_be_bytes()); // chan size
        c.extend_from_slice(b"chan");
        c.extend_from_slice(&0u32.to_be_bytes()); // version
        c.extend_from_slice(&0x7c0006u32.to_be_bytes()); // MPEG 5.1 tag
        c.extend_from_slice(&0u32.to_be_bytes()); // reserved
        c.extend_from_slice(&0u32.to_be_bytes()); // reserved
        assert_eq!(c.len(), 48);

        let cookie = MagicCookie::parse(&c).unwrap();
        let layout = cookie.layout.expect("chan extension parsed");
        assert_eq!(layout.tag, 0x7c0006);
        assert_eq!(layout.channels, 6);
        assert_eq!(layout.name, "MPEG_5_1_D");
    }

    #[test]
    fn rejects_bad_sizes_and_constraints() {
        assert!(MagicCookie::parse(&[0u8; 20]).is_err());
        let mut bad = core_cookie(16, 2);
        bad[5] = 33;
        assert!(MagicCookie::parse(&bad).is_err());
        let mut bad = core_cookie(16, 2);
        bad[0..4].copy_from_slice(&999u32.to_be_bytes());
        assert!(MagicCookie::parse(&bad).is_err());
    }

    #[test]
    fn rejects_chan_channel_mismatch() {
        let mut c = core_cookie(24, 2);
        c.extend_from_slice(&24u32.to_be_bytes());
        c.extend_from_slice(b"chan");
        c.extend_from_slice(&0u32.to_be_bytes());
        c.extend_from_slice(&0x640001u32.to_be_bytes()); // mono tag
        c.extend_from_slice(&0u32.to_be_bytes());
        c.extend_from_slice(&0u32.to_be_bytes());
        assert!(MagicCookie::parse(&c).is_err());
    }
}
