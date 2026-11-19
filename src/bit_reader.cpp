#include "bit_reader.h"

#include "common.h"

namespace hevclab {

BitReader::BitReader(const std::vector<std::uint8_t> &data, std::size_t bit_offset)
    : data_(data), bit_pos_(bit_offset) {}

std::uint32_t BitReader::read_bit() {
  if (bit_pos_ >= size()) {
    throw Error("unexpected end of RBSP");
  }
  const std::size_t byte = bit_pos_ >> 3;
  const int shift = 7 - static_cast<int>(bit_pos_ & 7);
  ++bit_pos_;
  return (data_[byte] >> shift) & 1u;
}

std::uint32_t BitReader::u(int bits) {
  if (bits < 0 || bits > 32) {
    throw Error("invalid u(n) width " + std::to_string(bits) + " at bit " +
                std::to_string(bit_pos_));
  }
  std::uint32_t value = 0;
  for (int i = 0; i < bits; ++i) {
    value = (value << 1) | read_bit();
  }
  return value;
}

bool BitReader::flag() { return u(1) != 0; }

void BitReader::skip(std::size_t bits) {
  if (bit_pos_ + bits > size()) {
    throw Error("unexpected end of RBSP while skipping");
  }
  bit_pos_ += bits;
}

std::uint32_t BitReader::ue() {
  int leading_zero_bits = 0;
  while (read_bit() == 0) {
    if (++leading_zero_bits >= 32) {
      throw Error("invalid Exp-Golomb code");
    }
  }
  if (leading_zero_bits == 0) {
    return 0;
  }
  return ((1u << leading_zero_bits) - 1u) + u(leading_zero_bits);
}

std::int32_t BitReader::se() {
  const std::uint32_t code_num = ue();
  if (code_num & 1u) {
    return static_cast<std::int32_t>((code_num + 1u) / 2u);
  }
  return -static_cast<std::int32_t>(code_num / 2u);
}

std::vector<std::uint8_t> remove_emulation_prevention(
    const std::uint8_t *data, std::size_t size) {
  std::vector<std::uint8_t> out;
  out.reserve(size);
  int zeros = 0;
  for (std::size_t i = 0; i < size; ++i) {
    const std::uint8_t byte = data[i];
    if (zeros >= 2 && byte == 0x03) {
      zeros = 0;
      continue;
    }
    out.push_back(byte);
    zeros = byte == 0 ? zeros + 1 : 0;
  }
  return out;
}

}  // namespace hevclab
