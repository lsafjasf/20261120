//! Minimal, dependency-free ISO-MP4 walker.
//!
//! It extracts only what the ALAC harness needs:
//!  - the `alac` magic cookie inside `stsd`,
//!  - `stsz` per-packet byte sizes,
//!  - `stco`/`co64` chunk offsets and `stsc` sample-to-chunk table,
//!  - `stts` timing (sample counts per delta),
//!  - `mdat` file ranges, so packets can be sliced out independently.

use std::io::{Read, Seek, SeekFrom};

use crate::{bail, Result};

#[derive(Debug, Clone)]
pub struct AtomHeader {
    pub fourcc: [u8; 4],
    pub start: u64,
    pub data_start: u64,
    pub data_end: u64,
}

impl AtomHeader {
    fn is_container(&self) -> bool {
        matches!(
            &self.fourcc,
            b"moov"
                | b"trak"
                | b"mdia"
                | b"minf"
                | b"stbl"
                | b"edts"
                | b"udta"
                | b"meta"
                | b"stsd"
                | b"wave"
        )
    }

    fn fullbox_header_size(&self) -> u64 {
        // FullBoxes carry one version+flags word after the plain atom header.
        match &self.fourcc {
            b"stts" | b"stsc" | b"stsz" | b"stco" | b"co64" | b"ctts" | b"stsd" => 4,
            _ => 0,
        }
    }
}

fn read_u32<R: Read + ?Sized>(r: &mut R) -> Result<u32> {
    let mut b = [0u8; 4];
    r.read_exact(&mut b)?;
    Ok(u32::from_be_bytes(b))
}

fn read_u64<R: Read + ?Sized>(r: &mut R) -> Result<u64> {
    let mut b = [0u8; 8];
    r.read_exact(&mut b)?;
    Ok(u64::from_be_bytes(b))
}

fn read_header<R: Seek + Read + ?Sized>(r: &mut R, end: u64) -> Result<Option<AtomHeader>> {
    let start = r.stream_position()?;
    if start >= end {
        return Ok(None);
    }
    if start + 8 > end {
        bail!("truncated atom header at offset {start}");
    }
    let size32 = read_u32(r)?;
    let mut fourcc = [0u8; 4];
    r.read_exact(&mut fourcc)?;
    let (data_start, data_end) = match size32 {
        0 => (start + 8, end),
        1 => {
            let largesize = read_u64(r)?;
            if largesize < 16 {
                bail!("invalid largesize for atom {}", fourcc_str(&fourcc));
            }
            (start + 16, start + largesize)
        }
        n if n >= 8 => (start + 8, start + u64::from(n)),
        n => bail!("invalid atom size {n} for {}", fourcc_str(&fourcc)),
    };
    if data_end > end {
        bail!("atom {} extends past parent end", fourcc_str(&fourcc));
    }
    Ok(Some(AtomHeader { fourcc, start, data_start, data_end }))
}

pub fn fourcc_str(f: &[u8; 4]) -> String {
    String::from_utf8_lossy(f).into_owned()
}

#[derive(Debug, Clone, Default)]
pub struct StscEntry {
    pub first_chunk: u32,
    pub samples_per_chunk: u32,
}

#[derive(Debug, Clone, Default)]
pub struct SttsEntry {
    pub sample_count: u32,
    pub sample_delta: u32,
}

#[derive(Debug, Clone, Default)]
pub struct Mp4Info {
    pub cookie: Vec<u8>,
    pub sample_sizes: Vec<u32>,
    pub chunk_offsets: Vec<u64>,
    pub stsc: Vec<StscEntry>,
    pub stts: Vec<SttsEntry>,
    pub mdat_ranges: Vec<(u64, u64)>,
}

impl Mp4Info {
    /// Expand chunk/stsc/stsz tables into (offset, size) for every packet.
    pub fn packet_ranges(&self) -> Result<Vec<(u64, u32)>> {
        if self.stsc.is_empty() {
            bail!("empty stsc table");
        }
        let mut ranges = Vec::with_capacity(self.sample_sizes.len());
        let mut sample_idx = 0usize;
        for (chunk_idx, &chunk_offset) in self.chunk_offsets.iter().enumerate() {
            let entry_idx = self
                .stsc
                .partition_point(|e| e.first_chunk as usize <= chunk_idx + 1)
                .saturating_sub(1);
            let per_chunk = self.stsc[entry_idx].samples_per_chunk as usize;
            let mut cursor = chunk_offset;
            for _ in 0..per_chunk {
                if sample_idx >= self.sample_sizes.len() {
                    bail!("stsc/stsz disagree: more samples in chunks than stsz entries");
                }
                let size = self.sample_sizes[sample_idx];
                ranges.push((cursor, size));
                cursor += u64::from(size);
                sample_idx += 1;
            }
        }
        if sample_idx != self.sample_sizes.len() {
            bail!(
                "stsc/stsz disagree: {} stsz entries but {} samples reached via chunks",
                self.sample_sizes.len(),
                sample_idx
            );
        }
        Ok(ranges)
    }

    pub fn timing_sample_count(&self) -> u64 {
        // Each entry applies `sample_count` packets, each lasting
        // `sample_delta` frames, so the track duration in frames is the
        // weighted sum.
        self.stts
            .iter()
            .map(|e| u64::from(e.sample_count) * u64::from(e.sample_delta))
            .sum()
    }
}

pub fn parse_mp4<R: Seek + Read + ?Sized>(r: &mut R) -> Result<Mp4Info> {
    let len = r.seek(SeekFrom::End(0))?;
    r.seek(SeekFrom::Start(0))?;
    let mut info = Mp4Info::default();
    walk(r, 0, len, &mut Vec::new(), &mut info)?;
    if info.cookie.is_empty() {
        bail!("no alac magic cookie found in stsd");
    }
    if info.sample_sizes.is_empty() {
        bail!("empty stsz table");
    }
    if info.chunk_offsets.is_empty() {
        bail!("empty chunk offset table");
    }
    Ok(info)
}

fn walk<R: Seek + Read + ?Sized>(
    r: &mut R,
    start: u64,
    end: u64,
    path: &mut Vec<String>,
    info: &mut Mp4Info,
) -> Result<()> {
    r.seek(SeekFrom::Start(start))?;
    while let Some(header) = read_header(r, end)? {
        path.push(fourcc_str(&header.fourcc));
        match &header.fourcc {
            b"mdat" => {
                info.mdat_ranges.push((header.data_start, header.data_end));
            }
            b"alac" => {
                // In an ALAC file the stsd sample entry is itself an `alac`
                // atom: skip its 28-byte AudioSampleEntry header and recurse
                // into the inner `alac` atom holding the magic cookie.
                if header.data_end - header.data_start > 28 {
                    walk(r, header.data_start + 28, header.data_end, path, info)?;
                } else {
                    parse_alac(r, &header, info)?;
                }
            }
            b"stsz" => parse_stsz(r, &header, info)?,
            b"stco" => parse_stco(r, &header, info, false)?,
            b"co64" => parse_stco(r, &header, info, true)?,
            b"stsc" => parse_stsc(r, &header, info)?,
            b"stts" => parse_stts(r, &header, info)?,
            _ => {
                if header.is_container() {
                    let child_start = if &header.fourcc == b"meta" {
                        header.data_start + 4
                    } else if &header.fourcc == b"stsd" {
                        // FullBox version/flags (4) + entry_count (4).
                        header.data_start + 8
                    } else {
                        header.data_start
                    };
                    walk(r, child_start, header.data_end, path, info)?;
                }
            }
        }
        let _ = path.pop();
        r.seek(SeekFrom::Start(header.data_end))?;
    }
    Ok(())
}

fn parse_alac<R: Read + Seek + ?Sized>(r: &mut R, header: &AtomHeader, info: &mut Mp4Info) -> Result<()> {
    r.seek(SeekFrom::Start(header.data_start))?;
    // version(1) + flags(3) precede the cookie inside the alac atom.
    let body_len = (header.data_end - header.data_start) as usize;
    if body_len < 4 {
        bail!("alac atom too small");
    }
    let mut body = vec![0u8; body_len];
    r.read_exact(&mut body)?;
    if body[0] != 0 || body[1..4] != [0, 0, 0] {
        bail!("alac atom: unsupported version/flags");
    }
    let cookie = body[4..].to_vec();
    if cookie.len() != 24 && cookie.len() != 48 {
        bail!("alac atom: cookie length {} is neither 24 nor 48", cookie.len());
    }
    info.cookie = cookie;
    Ok(())
}

fn parse_stsz<R: Read + Seek + ?Sized>(r: &mut R, header: &AtomHeader, info: &mut Mp4Info) -> Result<()> {
    r.seek(SeekFrom::Start(header.data_start + header.fullbox_header_size()))?;
    let _sample_size = read_u32(r)?;
    let count = read_u32(r)?;
    let mut sizes = Vec::with_capacity(count as usize);
    for _ in 0..count {
        sizes.push(read_u32(r)?);
    }
    info.sample_sizes = sizes;
    Ok(())
}

fn parse_stco<R: Read + Seek + ?Sized>(
    r: &mut R,
    header: &AtomHeader,
    info: &mut Mp4Info,
    wide: bool,
) -> Result<()> {
    // The walker positions the cursor at the atom data start.
    let prefix = header.fullbox_header_size();
    if prefix > 0 {
        let mut skip = [0u8; 4];
        r.read_exact(&mut skip)?;
    }
    let count = read_u32(r)?;
    let mut offsets = Vec::with_capacity(count as usize);
    for _ in 0..count {
        offsets.push(if wide { read_u64(r)? } else { u64::from(read_u32(r)?) });
    }
    info.chunk_offsets = offsets;
    Ok(())
}

fn parse_stsc<R: Read + Seek + ?Sized>(r: &mut R, header: &AtomHeader, info: &mut Mp4Info) -> Result<()> {
    r.seek(SeekFrom::Start(header.data_start + header.fullbox_header_size()))?;
    let count = read_u32(r)?;
    let mut entries = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let first_chunk = read_u32(r)?;
        let samples_per_chunk = read_u32(r)?;
        let _sample_desc_idx = read_u32(r)?;
        entries.push(StscEntry { first_chunk, samples_per_chunk });
    }
    info.stsc = entries;
    Ok(())
}

fn parse_stts<R: Read + Seek + ?Sized>(r: &mut R, header: &AtomHeader, info: &mut Mp4Info) -> Result<()> {
    r.seek(SeekFrom::Start(header.data_start + header.fullbox_header_size()))?;
    let count = read_u32(r)?;
    let mut entries = Vec::with_capacity(count as usize);
    for _ in 0..count {
        let sample_count = read_u32(r)?;
        let sample_delta = read_u32(r)?;
        entries.push(SttsEntry { sample_count, sample_delta });
    }
    info.stts = entries;
    Ok(())
}
