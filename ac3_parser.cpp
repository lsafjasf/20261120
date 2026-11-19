#include "ac3.hpp"

#include <algorithm>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <iostream>

using namespace ac3;

namespace {

struct Stats {
    int frames = 0;
    int crc_failed = 0;
    int dropped = 0;
    int size_errors = 0;
    int truncated = 0;
    size_t total_bytes = 0;
    uint64_t total_duration_us = 0;
    uint32_t peak_bitrate = 0;
    uint64_t bitrate_sum = 0;
};

std::vector<uint8_t> read_file(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) throw std::runtime_error("无法打开输入文件: " + path);
    return std::vector<uint8_t>((std::istreambuf_iterator<char>(in)),
                                std::istreambuf_iterator<char>());
}

size_t find_next_sync(const std::vector<uint8_t>& buf, size_t from) {
    if (from + 2 > buf.size()) return std::string::npos;
    for (size_t i = from; i + 2 <= buf.size(); ++i) {
        if (buf[i] == 0x0B && buf[i + 1] == 0x77) return i;
    }
    return std::string::npos;
}

void print_frame(int index, const FrameInfo& f) {
    std::cout << "帧 " << std::setw(3) << index
              << " | 偏移 0x" << std::hex << std::setw(6) << std::setfill('0')
              << f.offset << std::dec << std::setfill(' ')
              << " (" << std::setw(7) << f.offset << " B)"
              << " | bsid=" << static_cast<int>(f.bsid)
              << " | frmsizecod=" << std::setw(2)
              << static_cast<int>(f.frmsizecod)
              << (f.half_rate ? "(半码率)" : "      ")
              << " | " << f.sample_rate << " Hz"
              << " | " << f.channels << " ch (" << acmod_name(f.acmod)
              << (f.lfeon ? " + LFE" : "") << ")"
              << " | 帧长 " << std::setw(4) << f.size_bytes << " B"
              << " | 码率 " << std::setw(6) << f.nominal_bitrate << " bps"
              << " | CRC1 " << (f.crc1_ok ? "OK  " : "FAIL");
    if (!f.crc1_ok || !f.note.empty()) {
        std::cout << "  <-- " << (f.crc1_ok ? "" : "CRC 校验失败，失败帧偏移=0x");
        if (!f.crc1_ok)
            std::cout << std::hex << f.offset << std::dec;
        if (!f.note.empty()) std::cout << f.note;
    }
    std::cout << '\n';
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2 || argc > 3) {
        std::cerr << "用法: " << argv[0] << " <输入.ac3> [映射表.csv]\n";
        return 2;
    }
    std::vector<uint8_t> buf;
    try {
        buf = read_file(argv[1]);
    } catch (const std::exception& e) {
        std::cerr << e.what() << '\n';
        return 2;
    }
    const std::string csv_path = argc == 3 ? argv[2] : "frame_map.csv";

    Stats st;
    std::ofstream csv(csv_path, std::ios::binary);
    csv << "frame_index,byte_offset,offset_hex,size_bytes,channels,"
           "sample_rate_hz,bitrate_bps,crc1_ok,half_rate\n";

    std::cout << "文件: " << argv[1] << "  总字节数: " << buf.size() << "\n\n";

    size_t pos = 0;
    int index = 0;
    while (pos + 2 <= buf.size()) {
        size_t sync = find_next_sync(buf, pos);
        if (sync == std::string::npos) break;

        auto parsed = parse_header_at(buf, sync);
        if (!parsed) break;  // 尾部不足 7 字节，无法读出 BSI
        FrameInfo f = *parsed;

        if (!resolve_frame_size(f)) {
            ++st.size_errors;
            ++st.dropped;
            std::cout << "帧 " << std::setw(3) << index
                      << " | 偏移 0x" << std::hex << std::setw(6)
                      << std::setfill('0') << sync << std::dec
                      << std::setfill(' ')
                      << " | 帧长换算错误: fscod="
                      << static_cast<int>(f.fscod) << " frmsizecod="
                      << static_cast<int>(f.frmsizecod)
                      << " 超出 A/52 标准表，按漏帧处理并继续重同步\n";
            pos = sync + 2;
            ++index;
            continue;
        }

        if (sync + f.size_bytes > buf.size()) {
            ++st.truncated;
            ++st.dropped;
            std::cout << "帧 " << std::setw(3) << index
                      << " | 偏移 0x" << std::hex << std::setw(6)
                      << std::setfill('0') << sync << std::dec
                      << std::setfill(' ')
                      << " | 截断帧: 需要 " << f.size_bytes
                      << " B，剩余 " << (buf.size() - sync) << " B，按漏帧处理\n";
            break;
        }

        f.crc1_ok = verify_crc1(buf, f);
        if (!f.crc1_ok) ++st.crc_failed;

        ++st.frames;
        st.total_bytes += f.size_bytes;
        uint64_t dur_us = static_cast<uint64_t>(f.samples_per_frame) *
                          1000000ull / static_cast<uint64_t>(f.sample_rate);
        st.total_duration_us += dur_us;
        st.bitrate_sum += f.nominal_bitrate;
        st.peak_bitrate = std::max(st.peak_bitrate, f.nominal_bitrate);

        print_frame(index, f);
        csv << index << ',' << f.offset << ',' << "0x" << std::hex
            << f.offset << std::dec << ',' << f.size_bytes << ','
            << f.channels << ',' << f.sample_rate << ','
            << f.nominal_bitrate << ',' << (f.crc1_ok ? 1 : 0) << ','
            << (f.half_rate ? 1 : 0) << '\n';

        ++index;
        pos = sync + f.size_bytes;
    }

    uint32_t avg_nominal = st.frames ? static_cast<uint32_t>(
        st.bitrate_sum / static_cast<uint64_t>(st.frames)) : 0;
    double avg_time = st.total_duration_us
        ? static_cast<double>(st.total_bytes) * 8.0 * 1000000.0 /
              static_cast<double>(st.total_duration_us)
        : 0.0;

    std::cout << "\n===== 汇总 =====\n";
    std::cout << "完整解析帧数      : " << st.frames << '\n';
    std::cout << "CRC1 失败帧数     : " << st.crc_failed << '\n';
    std::cout << "漏帧总数          : " << st.dropped
              << " (帧长换算错误 " << st.size_errors
              << " + 截断 " << st.truncated << ")\n";
    std::cout << "帧长换算错误帧数  : " << st.size_errors << '\n';
    std::cout << "有效帧占用字节    : " << st.total_bytes << '\n';
    std::cout << "平均码率(标称均值): " << avg_nominal << " bps\n";
    std::cout << "平均码率(按时长) : " << std::fixed << std::setprecision(0)
              << avg_time << " bps\n";
    std::cout << "峰值码率          : " << st.peak_bitrate << " bps\n";
    std::cout << "帧序号->字节偏移映射表已写入: " << csv_path << '\n';
    csv.close();
    return 0;
}
