#include "ac3.hpp"

#include <cassert>
#include <iostream>

using namespace ac3;

int main() {
    const long sr[3] = {48000, 44100, 32000};
    const long br[19] = {
        32000, 40000, 48000, 56000, 64000, 80000, 96000, 112000, 128000,
        160000, 192000, 224000, 256000, 320000, 384000, 448000, 512000,
        576000, 640000};
    for (int i = 0; i < 19; ++i) {
        for (int half = 0; half < 2; ++half) {
            int code = i * 2 + half;
            const auto& row = FRAME_SIZE_TABLE[code];
            assert(row.bitrate == static_cast<uint32_t>(br[i]));
            for (int f = 0; f < 3; ++f) {
                long w = br[i] * 1536 / (sr[f] * 16);        // 三列均向下取整
                if (half && f == 1) w += 1;                   // 半码率 44.1k +1 word
                if (static_cast<long>(row.words[f]) != w) {
                    std::cout << "MISMATCH code=" << code << " fscod=" << f
                              << " table=" << row.words[f] << " formula=" << w
                              << '\n';
                    return 1;
                }
            }
        }
    }
    std::cout << "all 114 table entries match the A/52 formula\n";
}
