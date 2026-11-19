//! Minimal MSB-first bit reader used for ALAC packet header parsing.

#[derive(Debug)]
pub struct BitReader<'a> {
    data: &'a [u8],
    byte_pos: usize,
    bit_pos: u32,
}

impl<'a> BitReader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self { data, byte_pos: 0, bit_pos: 0 }
    }

    pub fn bits_consumed(&self) -> u64 {
        self.byte_pos as u64 * 8 + self.bit_pos as u64
    }

    pub fn read_bit(&mut self) -> Option<u32> {
        if self.byte_pos >= self.data.len() {
            return None;
        }
        let bit = (self.data[self.byte_pos] >> (7 - self.bit_pos)) & 1;
        self.bit_pos += 1;
        if self.bit_pos == 8 {
            self.bit_pos = 0;
            self.byte_pos += 1;
        }
        Some(u32::from(bit))
    }

    pub fn read_bits(&mut self, n: u32) -> Option<u32> {
        if n > 32 {
            return None;
        }
        let mut value = 0u32;
        for _ in 0..n {
            value = (value << 1) | self.read_bit()?;
        }
        Some(value)
    }

    /// Skip an arbitrary number of bits without materializing a value.
    pub fn skip_bits(&mut self, n: u64) -> Option<()> {
        let new_bit = self.bits_consumed().checked_add(n)?;
        if new_bit > (self.data.len() as u64) * 8 {
            return None;
        }
        self.byte_pos = (new_bit / 8) as usize;
        self.bit_pos = (new_bit % 8) as u32;
        Some(())
    }

    pub fn read_signed(&mut self, n: u32) -> Option<i32> {
        let raw = self.read_bits(n)?;
        if n == 0 {
            return Some(0);
        }
        let sign_bit = 1u32 << (n - 1);
        if raw & sign_bit != 0 {
            Some((raw as i32) - (1i32 << n))
        } else {
            Some(raw as i32)
        }
    }
}
