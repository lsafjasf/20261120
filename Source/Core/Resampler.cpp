#include "Core/Resampler.h"

#include <cmath>
#include <limits>

namespace abx
{
namespace
{
constexpr double pi = 3.14159265358979323846264338327950288;

float readOrZero(const std::vector<float>& samples, Frame index)
{
    if (index < 0 || index >= static_cast<Frame>(samples.size()))
        return 0.0f;

    return samples[static_cast<std::size_t>(index)];
}
} // namespace

AudioData resample(const AudioData& input, double targetSampleRate, ResampleOptions options)
{
    if (input.empty() || targetSampleRate <= 0.0)
        return {};

    if (std::abs(input.sampleRate - targetSampleRate) < 1.0e-9)
        return input;

    const auto inputFrames = input.numFrames();
    const double ratio = targetSampleRate / input.sampleRate;
    const auto outputFrames = std::max<Frame>(
        1, static_cast<Frame>(std::ceil(static_cast<double>(inputFrames) * ratio)));

    const int halfWidth = std::clamp(options.halfWidth, 4, 64);
    const double cutoff = std::clamp(ratio, 0.001, 1.0) * std::clamp(options.rolloff, 0.5, 1.0);
    const double radius = static_cast<double>(halfWidth) / cutoff;

    AudioData output;
    output.sampleRate = targetSampleRate;
    output.channels.resize(input.channels.size());

    for (std::size_t channelIndex = 0; channelIndex < input.channels.size(); ++channelIndex)
    {
        const auto& source = input.channels[channelIndex];
        auto& destination = output.channels[channelIndex];
        destination.resize(static_cast<std::size_t>(outputFrames));

        for (Frame outIndex = 0; outIndex < outputFrames; ++outIndex)
        {
            const double sourcePosition = static_cast<double>(outIndex) / ratio;
            const auto firstTap = static_cast<Frame>(std::ceil(sourcePosition - radius));
            const auto lastTap = static_cast<Frame>(std::floor(sourcePosition + radius));

            double weightSum = 0.0;
            double sampleSum = 0.0;

            for (Frame tap = firstTap; tap <= lastTap; ++tap)
            {
                const double distance = sourcePosition - static_cast<double>(tap);
                const double normalisedDistance = distance / radius;

                if (std::abs(normalisedDistance) >= 1.0)
                    continue;

                const double scaledDistance = cutoff * distance;
                const double sinc = std::abs(scaledDistance) < 1.0e-12
                                        ? 1.0
                                        : std::sin(pi * scaledDistance) / (pi * scaledDistance);
                const double window = 0.5 + 0.5 * std::cos(pi * normalisedDistance);
                const double weight = cutoff * sinc * window;

                weightSum += weight;
                sampleSum += weight * static_cast<double>(readOrZero(source, tap));
            }

            destination[static_cast<std::size_t>(outIndex)] =
                std::abs(weightSum) > std::numeric_limits<double>::epsilon()
                    ? static_cast<float>(sampleSum / weightSum)
                    : 0.0f;
        }
    }

    return output;
}

AudioData mixToStereo(const AudioData& input)
{
    if (input.empty())
        return {};

    if (input.numChannels() == 2)
        return input;

    AudioData output;
    output.sampleRate = input.sampleRate;
    output.channels.assign(2, {});
    output.channels[0].resize(static_cast<std::size_t>(input.numFrames()));
    output.channels[1].resize(static_cast<std::size_t>(input.numFrames()));

    if (input.numChannels() == 1)
    {
        output.channels[0] = input.channels[0];
        output.channels[1] = input.channels[0];
        return output;
    }

    const double gain = 1.0 / static_cast<double>(input.numChannels());

    for (std::size_t frame = 0; frame < input.channels[0].size(); ++frame)
    {
        double left = 0.0;
        double right = 0.0;

        for (int channel = 0; channel < input.numChannels(); ++channel)
        {
            const auto value = static_cast<double>(input.channels[static_cast<std::size_t>(channel)][frame]);
            left += value * gain;
            right += value * gain;
        }

        output.channels[0][frame] = static_cast<float>(left);
        output.channels[1][frame] = static_cast<float>(right);
    }

    return output;
}
} // namespace abx
