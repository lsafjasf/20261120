#pragma once

#include <cstdint>
#include <cstddef>
#include <optional>
#include <string>
#include <vector>

namespace ac3 {

constexpr uint16_t SYNC_WORD = 0x0B77;

// ATSC A/52:2018 Table 5.18 "Frame size code".
// 每个 frmsizecod 对应一行，数值单位为 16-bit 字（word = 2 字节）。
// 三列依次对应 fscod = 0 (48 kHz), 1 (44.1 kHz), 2 (32 kHz)。
// 奇数 frmsizecod 为半码率编码，44.1 kHz 列比相邻偶数代码多 1 个 word。
struct FrameSizeRow {
    uint32_t bitrate;
    uint16_t words[3];
};

constexpr FrameSizeRow FRAME_SIZE_TABLE[38] = {
    {  32000, {  64,   69,   96} }, {  32000, {  64,   70,   96} },
    {  40000, {  80,   87,  120} }, {  40000, {  80,   88,  120} },
    {  48000, {  96,  104,  144} }, {  48000, {  96,  105,  144} },
    {  56000, { 112,  121,  168} }, {  56000, { 112,  122,  168} },
    {  64000, { 128,  139,  192} }, {  64000, { 128,  140,  192} },
    {  80000, { 160,  174,  240} }, {  80000, { 160,  175,  240} },
    {  96000, { 192,  208,  288} }, {  96000, { 192,  209,  288} },
    { 112000, { 224,  243,  336} }, { 112000, { 224,  244,  336} },
    { 128000, { 256,  278,  384} }, { 128000, { 256,  279,  384} },
    { 160000, { 320,  348,  480} }, { 160000, { 320,  349,  480} },
    { 192000, { 384,  417,  576} }, { 192000, { 384,  418,  576} },
    { 224000, { 448,  487,  672} }, { 224000, { 448,  488,  672} },
    { 256000, { 512,  557,  768} }, { 256000, { 512,  558,  768} },
    { 320000, { 640,  696,  960} }, { 320000, { 640,  697,  960} },
    { 384000, { 768,  835, 1152} }, { 384000, { 768,  836, 1152} },
    { 448000, { 896,  975, 1344} }, { 448000, { 896,  976, 1344} },
    { 512000, {1024, 1114, 1536} }, { 512000, {1024, 1115, 1536} },
    { 576000, {1152, 1253, 1728} }, { 576000, {1152, 1254, 1728} },
    { 640000, {1280, 1393, 1920} }, { 640000, {1280, 1394, 1920} },
};

constexpr int MAX_FRMSIZECOD = 37;

inline int sample_rate_hz(uint8_t fscod) {
    static const int sr[3] = {48000, 44100, 32000};
    return fscod < 3 ? sr[fscod] : 0;
}

inline const char* acmod_name(uint8_t acmod) {
    static const char* names[8] = {
        "1+1 (dual mono)", "1/0 (mono)", "2/0 (stereo)", "3/0",
        "2/1", "3/1", "2/2", "3/2 (5.1 layout)"
    };
    return names[acmod & 7];
}

inline int acmod_fullband_channels(uint8_t acmod) {
    static const int ch[8] = {2, 1, 2, 3, 3, 4, 4, 5};
    return ch[acmod & 7];
}

// 大端优先的 MSB-first 位读取器，用于 Bit Stream Information 段。
class BitReader {
public:
    BitReader(const uint8_t* data, size_t size_bits)
        : data_(data), remain_(size_bits) {}

    uint32_t read(unsigned bits) {
        uint32_t value = 0;
        while (bits--) {
            if (remain_ == 0) return value;
            unsigned byte = data_[pos_ >> 3];
            unsigned bit = (byte >> (7 - (pos_ & 7))) & 1u;
            value = (value << 1) | bit;
            ++pos_;
            --remain_;
        }
        return value;
    }

private:
    const uint8_t* data_;
    size_t pos_ = 0;
    size_t remain_;
};

// A/52 规定的 CRC-16：poly=0x8005，MSB-first，初值 0，不反射，无最终异或。
inline uint16_t crc16(const uint8_t* data, size_t length, uint16_t state = 0) {
    for (size_t i = 0; i < length; ++i) {
        state ^= static_cast<uint16_t>(data[i]) << 8;
        for (int b = 0; b < 8; ++b) {
            if (state & 0x8000)
                state = static_cast<uint16_t>((state << 1) ^ 0x8005);
            else
                state = static_cast<uint16_t>(state << 1);
        }
    }
    return state;
}

struct FrameInfo {
    size_t offset = 0;
    uint8_t bsid = 0;
    uint8_t bsmod = 0;
    uint8_t fscod = 0;
    uint8_t frmsizecod = 0;
    uint8_t acmod = 0;
    bool lfeon = false;
    int sample_rate = 0;
    int channels = 0;
    uint32_t nominal_bitrate = 0;
    size_t size_words = 0;
    size_t size_bytes = 0;
    int samples_per_frame = 0;
    bool half_rate = false;
    bool crc1_ok = false;
    std::string note;
};

// 仅在 offset 处解析同步头与 BSI，不负责寻找同步字。
// 返回 std::nullopt 表示连最小头部都读不出来（文件尾部）。
inline std::optional<FrameInfo> parse_header_at(const std::vector<uint8_t>& buf,
                                                size_t offset) {
    if (offset + 7 > buf.size()) return std::nullopt;

    FrameInfo f;
    f.offset = offset;
    uint16_t sync = static_cast<uint16_t>((buf[offset] << 8) | buf[offset + 1]);
    if (sync != SYNC_WORD) return f;

    f.fscod = static_cast<uint8_t>((buf[offset + 4] >> 6) & 0x03);
    f.frmsizecod = static_cast<uint8_t>(buf[offset + 4] & 0x3F);

    // BSI 从第 5 个字节开始（syncinfo 共 48 bit）。
    BitReader br(buf.data() + offset + 6, (buf.size() - offset - 6) * 8);
    f.bsid = static_cast<uint8_t>(br.read(5));
    f.bsmod = static_cast<uint8_t>(br.read(3));
    f.acmod = static_cast<uint8_t>(br.read(3));
    if (f.acmod & 1) br.read(2);                 // cmixlev
    if (f.acmod == 0) br.read(2);                // dsurmod
    if (f.acmod & 4) br.read(2);                 // surmixlev
    f.lfeon = br.read(1) != 0;

    f.sample_rate = sample_rate_hz(f.fscod);
    f.channels = acmod_fullband_channels(f.acmod) + (f.lfeon ? 1 : 0);
    f.half_rate = (f.frmsizecod & 1) != 0;
    f.samples_per_frame = f.half_rate ? 3072 : 1536;
    return f;
}

// 按标准表把 frmsizecod/fscod 换算成字节数；非法代码返回 false。
inline bool resolve_frame_size(FrameInfo& f) {
    if (f.fscod >= 3 || f.frmsizecod > MAX_FRMSIZECOD) return false;
    const FrameSizeRow& row = FRAME_SIZE_TABLE[f.frmsizecod];
    f.size_words = row.words[f.fscod];
    f.size_bytes = f.size_words * 2;
    f.nominal_bitrate = row.bitrate;
    return true;
}

// CRC1 覆盖帧的前 5/8 字节，CRC1 字段自身（偏移 2..3）按 0 参与计算。
inline bool verify_crc1(const std::vector<uint8_t>& buf, const FrameInfo& f) {
    size_t crc_len = f.size_bytes * 5 / 8;
    if (f.offset + crc_len > buf.size()) return false;
    const uint8_t* p = buf.data() + f.offset;
    uint16_t state = crc16(p, 2);
    uint8_t zero[2] = {0, 0};
    state = crc16(zero, 2, state);
    state = crc16(p + 4, crc_len - 4, state);
    uint16_t stored = static_cast<uint16_t>((p[2] << 8) | p[3]);
    return state == stored;
}

}  // namespace ac3
