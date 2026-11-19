#include "AudioPlaybackEngine.h"

#include <algorithm>
#include <cmath>

namespace abxapp
{
AudioPlaybackEngine::AudioPlaybackEngine() = default;

AudioPlaybackEngine::~AudioPlaybackEngine()
{
    if (callbackRegistered_)
        deviceManager_.removeAudioCallback(this);

    deviceManager_.closeAudioDevice();
}

juce::String AudioPlaybackEngine::initialiseDevice()
{
    const auto error = deviceManager_.initialiseWithDefaultDevices(0, 2);
    if (error.isEmpty() && !callbackRegistered_)
    {
        deviceManager_.addAudioCallback(this);
        callbackRegistered_ = true;
    }

    return error;
}

bool AudioPlaybackEngine::hasAudioDevice() const
{
    return deviceManager_.getCurrentAudioDevice() != nullptr;
}

double AudioPlaybackEngine::currentSampleRate()
{
    if (auto* device = deviceManager_.getCurrentAudioDevice())
        return device->getCurrentSampleRate();

    return 0.0;
}

void AudioPlaybackEngine::prepare(abx::AudioData reference, abx::AudioData comparison)
{
    if (callbackRegistered_)
        deviceManager_.removeAudioCallback(this);

    prepared_ = false;
    sources_[0] = std::move(reference);
    sources_[1] = std::move(comparison);
    sourceFrames_ = sources_[0].numFrames();
    requestedSource_.store(0);
    activeSource_ = 0;
    playing_.store(false);
    xUsesA_.store(true);
    pendingXUsesA_.store(true);
    xMappingUpdatePending_.store(false);
    fadeActive_ = false;
    playhead_.store(0);
    selectionStart_.store(0);
    selectionEnd_.store(sourceFrames_);
    prepared_ = true;

    if (callbackRegistered_)
        deviceManager_.addAudioCallback(this);
}

void AudioPlaybackEngine::clear()
{
    if (callbackRegistered_)
        deviceManager_.removeAudioCallback(this);

    playing_.store(false);
    prepared_ = false;
    sources_ = {};
    sourceFrames_ = 0;
    playhead_.store(0);
    selectionStart_.store(0);
    selectionEnd_.store(0);
    fadeActive_ = false;
    xMappingUpdatePending_.store(false);

    if (callbackRegistered_)
        deviceManager_.addAudioCallback(this);
}

void AudioPlaybackEngine::play()
{
    if (!prepared_)
        return;

    const auto start = selectionStart_.load();
    const auto end = selectionEnd_.load();
    const auto current = playhead_.load();
    if (current < start || current >= end)
        playhead_.store(start);

    playing_.store(true);
}

void AudioPlaybackEngine::pause()
{
    playing_.store(false);
}

bool AudioPlaybackEngine::isPlaying() const
{
    return playing_.load();
}

void AudioPlaybackEngine::setSource(Audition source)
{
    requestedSource_.store(static_cast<int>(source));
}

Audition AudioPlaybackEngine::requestedSource() const
{
    return static_cast<Audition>(requestedSource_.load());
}

void AudioPlaybackEngine::setXUsesA(bool shouldUseA)
{
    pendingXUsesA_.store(shouldUseA);
    xMappingUpdatePending_.store(true);
}

void AudioPlaybackEngine::setSelection(abx::Frame start, abx::Frame end)
{
    if (sourceFrames_ <= 1)
        return;

    const auto clampedStart = std::clamp<abx::Frame>(start, 0, sourceFrames_);
    const auto clampedEnd = std::clamp<abx::Frame>(end, clampedStart + 1, sourceFrames_);
    selectionStart_.store(clampedStart);
    selectionEnd_.store(clampedEnd);
}

abx::Frame AudioPlaybackEngine::selectionStart() const
{
    return selectionStart_.load();
}

abx::Frame AudioPlaybackEngine::selectionEnd() const
{
    return selectionEnd_.load();
}

void AudioPlaybackEngine::setLooping(bool shouldLoop)
{
    looping_.store(shouldLoop);
}

bool AudioPlaybackEngine::isLooping() const
{
    return looping_.load();
}

void AudioPlaybackEngine::setPosition(abx::Frame frame)
{
    playhead_.store(std::clamp<abx::Frame>(frame, 0, std::max<abx::Frame>(0, sourceFrames_ - 1)));
}

abx::Frame AudioPlaybackEngine::position() const
{
    return playhead_.load();
}

abx::Frame AudioPlaybackEngine::totalFrames() const
{
    return sourceFrames_;
}

float AudioPlaybackEngine::sampleAt(Audition source,
                                    abx::Frame frame,
                                    int outputChannel) const
{
    if (frame < 0 || frame >= sourceFrames_)
        return 0.0f;

    const int sourceIndex = source == Audition::B
                                ? 1
                                : (source == Audition::X ? (xUsesA_.load() ? 0 : 1) : 0);

    const auto& audio = sources_[static_cast<std::size_t>(sourceIndex)];
    if (audio.numChannels() == 0)
        return 0.0f;

    if (outputChannel >= audio.numChannels())
        outputChannel = audio.numChannels() - 1;

    if (outputChannel < 0)
        return 0.0f;

    return audio.channels[static_cast<std::size_t>(outputChannel)]
                         [static_cast<std::size_t>(frame)];
}

int AudioPlaybackEngine::transitionLengthFor(abx::Frame selectionLength) const
{
    if (selectionLength <= 0)
        return std::max(1, fadeFrames_);

    const auto quarter = static_cast<int>(
        std::max<abx::Frame>(1, selectionLength / 4));
    return std::clamp(std::min(fadeFrames_, quarter), 1, std::max(1, fadeFrames_));
}

void AudioPlaybackEngine::audioDeviceIOCallbackWithContext(
    const float* const*,
    int,
    float* const* outputChannelData,
    int numOutputChannels,
    int numSamples,
    const juce::AudioIODeviceCallbackContext&)
{
    for (int channel = 0; channel < numOutputChannels; ++channel)
    {
        if (outputChannelData[channel] != nullptr)
            juce::FloatVectorOperations::clear(outputChannelData[channel], numSamples);
    }

    if (!prepared_ || sourceFrames_ <= 1 || numSamples <= 0)
        return;

    const auto requested = static_cast<int>(requestedSource_.load());
    if (!playing_.load())
    {
        if (xMappingUpdatePending_.exchange(false))
            xUsesA_.store(pendingXUsesA_.load());

        fadeActive_ = false;
        activeSource_ = requested;
        return;
    }

    const auto selectionStart = std::clamp<abx::Frame>(
        selectionStart_.load(), 0, sourceFrames_ - 1);
    const auto selectionEnd = std::clamp<abx::Frame>(
        selectionEnd_.load(), selectionStart + 1, sourceFrames_);

    auto currentPosition = playhead_.load();
    if (currentPosition < selectionStart || currentPosition >= selectionEnd)
    {
        currentPosition = selectionStart;
        fadeActive_ = false;
    }

    const bool looping = looping_.load();
    const int loopTransition = transitionLengthFor(selectionEnd - selectionStart);
    const int sourceTransition = std::max(1, fadeFrames_);
    bool stillPlaying = true;

    for (int sampleIndex = 0; sampleIndex < numSamples; ++sampleIndex)
    {
        if (!fadeActive_)
        {
            if (currentPosition >= selectionEnd)
            {
                if (looping)
                {
                    currentPosition = selectionStart;
                }
                else
                {
                    stillPlaying = false;
                    break;
                }
            }

            const bool shouldLoop = looping
                                    && currentPosition > selectionStart
                                    && selectionEnd - currentPosition <= loopTransition;
            const bool shouldSwitchSource = requested != activeSource_;

            if (shouldLoop)
            {
                fadeActive_ = true;
                fadeFromSource_ = activeSource_;
                fadeToSource_ = shouldSwitchSource ? requested : activeSource_;
                fadeFromPosition_ = currentPosition;
                fadeToPosition_ = selectionStart;
                fadePosition_ = 0;
                fadeLength_ = loopTransition;
            }
            else if (shouldSwitchSource)
            {
                fadeActive_ = true;
                fadeFromSource_ = activeSource_;
                fadeToSource_ = requested;
                fadeFromPosition_ = currentPosition;
                fadeToPosition_ = currentPosition;
                fadePosition_ = 0;
                fadeLength_ = sourceTransition;
            }
        }

        float left = 0.0f;
        float right = 0.0f;

        if (fadeActive_)
        {
            const float mix = static_cast<float>(fadePosition_)
                              / static_cast<float>(std::max<abx::Frame>(1, fadeLength_));
            const float fromGain = 1.0f - mix;
            const float toGain = mix;

            const auto from = static_cast<Audition>(fadeFromSource_);
            const auto to = static_cast<Audition>(fadeToSource_);
            left = sampleAt(from, fadeFromPosition_, 0) * fromGain
                   + sampleAt(to, fadeToPosition_, 0) * toGain;
            right = sampleAt(from, fadeFromPosition_, 1) * fromGain
                    + sampleAt(to, fadeToPosition_, 1) * toGain;

            ++fadeFromPosition_;
            ++fadeToPosition_;
            ++fadePosition_;

            if (fadePosition_ >= fadeLength_)
            {
                activeSource_ = fadeToSource_;
                currentPosition = fadeToPosition_;
                fadeActive_ = false;
            }
        }
        else
        {
            const auto source = static_cast<Audition>(activeSource_);
            left = sampleAt(source, currentPosition, 0);
            right = sampleAt(source, currentPosition, 1);
            ++currentPosition;
        }

        if (numOutputChannels == 1)
        {
            outputChannelData[0][sampleIndex] = 0.5f * (left + right);
        }
        else
        {
            if (outputChannelData[0] != nullptr)
                outputChannelData[0][sampleIndex] = left;
            if (outputChannelData[1] != nullptr)
                outputChannelData[1][sampleIndex] = right;

            for (int channel = 2; channel < numOutputChannels; ++channel)
            {
                if (outputChannelData[channel] != nullptr)
                    outputChannelData[channel][sampleIndex] = 0.5f * (left + right);
            }
        }
    }

    if (stillPlaying && !fadeActive_ && currentPosition >= selectionEnd && !looping)
        stillPlaying = false;

    playhead_.store(std::clamp<abx::Frame>(currentPosition, 0, sourceFrames_));
    if (!stillPlaying)
        playing_.store(false);
}

void AudioPlaybackEngine::audioDeviceAboutToStart(juce::AudioIODevice* device)
{
    if (device != nullptr)
        fadeFrames_ = std::max(1, juce::roundToInt(device->getCurrentSampleRate() * 0.010));
}

void AudioPlaybackEngine::audioDeviceStopped()
{
    playing_.store(false);
}
} // namespace abxapp
