#include "ac3.hpp"

#include <fstream>
#include <iomanip>
#include <iostream>

using namespace ac3;

namespace {

struct Spec {
    uint8_t frmsizecod;
    uint8_t acmod;
    bool lfeon;
};

uint16_t compute_crc1(const std::vector<uint8_t>& frame) {
    size_t crc_len = frame.size() * 5 / 8;
    uint16_t state = crc16(frame.data(), 2);
    const uint8_t zero[2] = {0, 0};
    state = crc16(zero, 2, state);
    state = crc16(frame.data() + 4, crc_len - 4, state);
    return state;
}

std::vector<uint8_t> build_frame(const Spec& spec, int serial) {
    FrameInfo probe;
    probe.fscod = 0;
    probe.frmsizecod = spec.frmsizecod;
    if (!resolve_frame_size(probe)) throw std::runtime_error("非法 frmsizecod");

    std::vector<uint8_t> f(probe.size_bytes, 0);
    f[0] = 0x0B;
    f[1] = 0x77;
    f[4] = spec.frmsizecod;  // fscod=00，低 6 位为 frmsizecod

    // BSI: bsid=8, bsmod=0
    f[6] = static_cast<uint8_t>(8 << 3);
    // 手工拼装 BSI 后续位: acmod(3) + 可选电平位 + lfeon(1)
    uint8_t byte7 = static_cast<uint8_t>(spec.acmod << 5);
    unsigned shift = 4;  // acmod 占 3 bit (7..5) 后，下一字段位号为 4
    if (spec.acmod & 1) shift -= 2;       // cmixlev
    if (spec.acmod == 0) shift -= 2;      // dsurmod
    if (spec.acmod & 4) shift -= 2;       // surmixlev
    if (spec.lfeon) byte7 |= static_cast<uint8_t>(1u << shift);
    f[7] = byte7;

    // 确定性填充，避免依赖随机数，保证样例可复现
    for (size_t i = 8; i < f.size(); ++i) {
        f[i] = static_cast<uint8_t>((serial * 131 + static_cast<int>(i) * 73 + 29) & 0xFF);
    }

    uint16_t crc = compute_crc1(f);
    f[2] = static_cast<uint8_t>(crc >> 8);
    f[3] = static_cast<uint8_t>(crc & 0xFF);
    return f;
}

}  // namespace

int main(int argc, char** argv) {
    const std::string path = argc > 1 ? argv[1] : "sample.ac3";
    std::vector<uint8_t> out;

    std::cout << "样例布局 (48 kHz, bsid=8)\n";
    std::cout << "序号  偏移      类型                frmsizecod  帧长(B)  预期 CRC\n";

    for (int i = 0; i < 12; ++i) {
        bool stereo = (i % 2 == 0);
        Spec spec = stereo ? Spec{20, 2, false}   // 2/0, 192 kbps, 320 B
                           : Spec{30, 7, true};   // 3/2+LFE, 448 kbps, 448 B
        std::vector<uint8_t> f = build_frame(spec, i);
        size_t off = out.size();

        if (i == 5) {
            // 破坏第 6 帧（0 基序号 5）CRC 覆盖区内的一个数据字节
            f[8] ^= 0xFF;
        }
        out.insert(out.end(), f.begin(), f.end());

        std::cout << std::setw(3) << i << "  0x" << std::hex << std::setw(6)
                  << std::setfill('0') << off << std::dec << std::setfill(' ')
                  << "  " << (stereo ? "2.0 (2/0)        " : "5.1 (3/2 + LFE)  ")
                  << std::setw(9) << static_cast<int>(spec.frmsizecod)
                  << std::setw(10) << f.size()
                  << (i == 5 ? "  FAIL(人为破坏)" : "  OK") << '\n';

        if (i == 7) {
            // 假同步: 0x0B77 + frmsizecod=63（标准表只定义到 37），共 8 字节垃圾
            size_t junk_off = out.size();
            const uint8_t junk[8] = {0x0B, 0x77, 0x00, 0x00,
                                     0x3F, 0x00, 0x40, 0x00};
            out.insert(out.end(), junk, junk + 8);
            std::cout << "  -  0x" << std::hex << std::setw(6)
                      << std::setfill('0') << junk_off << std::dec
                      << std::setfill(' ')
                      << "  假同步/非法 frmsizecod=63，8 B，按漏帧处理\n";
        }
    }

    // 尾部截断帧：只写入一个 192 kbps 帧的前 40 字节
    Spec tail{20, 2, false};
    std::vector<uint8_t> tf = build_frame(tail, 99);
    size_t tail_off = out.size();
    out.insert(out.end(), tf.begin(), tf.begin() + 40);
    std::cout << "  T  0x" << std::hex << std::setw(6) << std::setfill('0')
              << tail_off << std::dec << std::setfill(' ')
              << "  截断帧: 仅 40/320 B，按漏帧处理\n";

    std::ofstream os(path, std::ios::binary);
    os.write(reinterpret_cast<const char*>(out.data()),
             static_cast<std::streamsize>(out.size()));
    std::cout << "\n已写出 " << path << " (" << out.size() << " 字节)\n";
    return 0;
}
