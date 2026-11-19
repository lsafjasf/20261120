#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace hevclab {

class BitReader {
 public:
  BitReader(const std::vector<std::uint8_t> &data, std::size_t bit_offset = 0);

  std::uint32_t u(int bits);
  std::uint32_t ue();
  std::int32_t se();
  bool flag();
  void skip(std::size_t bits);

  std::size_t position() const { return bit_pos_; }
  std::size_t size() const { return data_.size() * 8; }
  bool eof() const { return bit_pos_ >= size(); }

 private:
  std::uint32_t read_bit();

  const std::vector<std::uint8_t> &data_;
  std::size_t bit_pos_ = 0;
};

std::vector<std::uint8_t> remove_emulation_prevention(
    const std::uint8_t *data, std::size_t size);

}  // namespace hevclab
