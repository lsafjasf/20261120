#pragma once

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

namespace abx
{
using Frame = std::int64_t;

struct AudioData
{
    double sampleRate = 0.0;
    std::vector<std::vector<float>> channels;

    [[nodiscard]] int numChannels() const noexcept
    {
        return static_cast<int>(channels.size());
    }

    [[nodiscard]] Frame numFrames() const noexcept
    {
        if (channels.empty())
            return 0;

        return static_cast<Frame>(channels.front().size());
    }

    [[nodiscard]] bool empty() const noexcept
    {
        return sampleRate <= 0.0 || numChannels() == 0 || numFrames() == 0;
    }
};

struct Selection
{
    Frame alignedStart = 0;
    Frame alignedEnd = 0;
    Frame sourceAStart = 0;
    Frame sourceAEnd = 0;
    Frame sourceBStart = 0;
    Frame sourceBEnd = 0;
    double sampleRate = 0.0;

    [[nodiscard]] bool valid() const noexcept
    {
        return sampleRate > 0.0 && alignedStart >= 0 && alignedEnd > alignedStart
               && sourceAEnd > sourceAStart && sourceBEnd > sourceBStart;
    }

    [[nodiscard]] Frame length() const noexcept
    {
        return alignedEnd - alignedStart;
    }
};

struct AlignmentResult
{
    bool ok = false;
    Frame lagSamples = 0;
    Frame commonStartA = 0;
    Frame commonEndA = 0;
    Frame commonStartB = 0;
    Frame commonEndB = 0;
    double sampleRate = 0.0;
    double confidence = 0.0;
    std::string error;

    [[nodiscard]] Frame commonFrames() const noexcept
    {
        return commonEndA - commonStartA;
    }

    [[nodiscard]] double commonDurationSeconds() const noexcept
    {
        return sampleRate > 0.0 ? static_cast<double>(commonFrames()) / sampleRate : 0.0;
    }

    [[nodiscard]] double delaySeconds() const noexcept
    {
        return sampleRate > 0.0 ? static_cast<double>(lagSamples) / sampleRate : 0.0;
    }
};
} // namespace abx
