#include "Core/WavIO.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <fstream>
#include <limits>

namespace abx
{
namespace
{
std::uint32_t readU32(const unsigned char* bytes)
{
    return static_cast<std::uint32_t>(bytes[0])
           | (static_cast<std::uint32_t>(bytes[1]) << 8U)
           | (static_cast<std::uint32_t>(bytes[2]) << 16U)
           | (static_cast<std::uint32_t>(bytes[3]) << 24U);
}

std::uint16_t readU16(const unsigned char* bytes)
{
    return static_cast<std::uint16_t>(bytes[0])
           | (static_cast<std::uint16_t>(bytes[1]) << 8U);
}

void writeU32(std::ofstream& output, std::uint32_t value)
{
    const unsigned char bytes[4] = {
        static_cast<unsigned char>(value & 0xffU),
        static_cast<unsigned char>((value >> 8U) & 0xffU),
        static_cast<unsigned char>((value >> 16U) & 0xffU),
        static_cast<unsigned char>((value >> 24U) & 0xffU)};
    output.write(reinterpret_cast<const char*>(bytes), 4);
}

void writeU16(std::ofstream& output, std::uint16_t value)
{
    const unsigned char bytes[2] = {
        static_cast<unsigned char>(value & 0xffU),
        static_cast<unsigned char>((value >> 8U) & 0xffU)};
    output.write(reinterpret_cast<const char*>(bytes), 2);
}

bool readChunkHeader(std::ifstream& input, char id[5], std::uint32_t& size)
{
    unsigned char bytes[8] {};
    input.read(reinterpret_cast<char*>(bytes), 8);
    if (!input)
        return false;

    std::memcpy(id, bytes, 4);
    id[4] = '\0';
    size = readU32(bytes + 4);
    return true;
}
} // namespace

bool readPcm16Wav(const std::string& path, AudioData& audio, std::string& error)
{
    std::ifstream input(path, std::ios::binary);
    if (!input)
    {
        error = "无法打开 WAV 文件。";
        return false;
    }

    char riff[5] {};
    std::uint32_t riffSize = 0;
    if (!readChunkHeader(input, riff, riffSize) || std::strcmp(riff, "RIFF") != 0)
    {
        error = "不是 RIFF 文件。";
        return false;
    }

    char wave[5] {};
    input.read(wave, 4);
    if (!input || std::strcmp(wave, "WAVE") != 0)
    {
        error = "不是 WAVE 文件。";
        return false;
    }

    std::uint16_t channels = 0;
    std::uint16_t bitsPerSample = 0;
    std::uint32_t sampleRate = 0;
    std::vector<unsigned char> data;

    while (input)
    {
        char id[5] {};
        std::uint32_t size = 0;
        if (!readChunkHeader(input, id, size))
            break;

        if (std::strcmp(id, "fmt ") == 0)
        {
            std::vector<unsigned char> format(size);
            input.read(reinterpret_cast<char*>(format.data()), static_cast<std::streamsize>(size));
            if (format.size() < 16)
            {
                error = "WAV fmt 块损坏。";
                return false;
            }

            if (readU16(format.data()) != 1)
            {
                error = "测试 WAV 读取器只支持 PCM。";
                return false;
            }

            channels = readU16(format.data() + 2);
            sampleRate = readU32(format.data() + 4);
            bitsPerSample = readU16(format.data() + 14);
            if ((size & 1U) != 0)
                input.seekg(1, std::ios::cur);
        }
        else if (std::strcmp(id, "data") == 0)
        {
            data.resize(size);
            input.read(reinterpret_cast<char*>(data.data()), static_cast<std::streamsize>(size));
        }
        else
        {
            input.seekg(static_cast<std::streamoff>(size + (size & 1U)), std::ios::cur);
        }
    }

    if (channels == 0 || bitsPerSample != 16 || sampleRate == 0 || data.empty())
    {
        error = "WAV 缺少有效的 PCM16 音频数据。";
        return false;
    }

    const auto frames = data.size() / (static_cast<std::size_t>(channels) * 2U);
    audio.sampleRate = static_cast<double>(sampleRate);
    audio.channels.assign(channels, {});

    for (auto& channel : audio.channels)
        channel.resize(frames);

    for (std::size_t frame = 0; frame < frames; ++frame)
    {
        for (std::uint16_t channel = 0; channel < channels; ++channel)
        {
            const auto offset = (frame * channels + channel) * 2U;
            const auto value = static_cast<std::int16_t>(readU16(data.data() + offset));
            audio.channels[channel][frame] = static_cast<float>(value) / 32768.0f;
        }
    }

    error.clear();
    return true;
}

bool writePcm16Wav(const std::string& path, const AudioData& audio, std::string& error)
{
    if (audio.empty() || audio.sampleRate <= 0.0 || audio.sampleRate > 384000.0)
    {
        error = "音频数据无效。";
        return false;
    }

    const auto frames = audio.numFrames();
    const auto channels = audio.numChannels();
    const auto dataBytes = frames * channels * 2;
    if (dataBytes > static_cast<Frame>(std::numeric_limits<std::uint32_t>::max() - 36))
    {
        error = "音频过长，无法写入 PCM16 WAV。";
        return false;
    }

    std::ofstream output(path, std::ios::binary);
    if (!output)
    {
        error = "无法创建 WAV 文件。";
        return false;
    }

    output.write("RIFF", 4);
    writeU32(output, static_cast<std::uint32_t>(dataBytes + 36));
    output.write("WAVE", 4);
    output.write("fmt ", 4);
    writeU32(output, 16);
    writeU16(output, 1);
    writeU16(output, static_cast<std::uint16_t>(channels));
    writeU32(output, static_cast<std::uint32_t>(audio.sampleRate + 0.5));
    writeU32(output, static_cast<std::uint32_t>(audio.sampleRate + 0.5)
                         * static_cast<std::uint32_t>(channels) * 2U);
    writeU16(output, static_cast<std::uint16_t>(channels * 2));
    writeU16(output, 16);
    output.write("data", 4);
    writeU32(output, static_cast<std::uint32_t>(dataBytes));

    for (Frame frame = 0; frame < frames; ++frame)
    {
        for (int channel = 0; channel < channels; ++channel)
        {
            const auto sample = std::clamp(
                audio.channels[static_cast<std::size_t>(channel)][static_cast<std::size_t>(frame)],
                -1.0f, 1.0f);
            const auto value = static_cast<std::uint16_t>(
                static_cast<std::int16_t>(std::lrint(sample * 32767.0f)));
            writeU16(output, value);
        }
    }

    if (!output)
    {
        error = "写入 WAV 文件失败。";
        return false;
    }

    error.clear();
    return true;
}
} // namespace abx
