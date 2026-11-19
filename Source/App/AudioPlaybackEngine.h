#pragma once

#include "Core/Assignment.h"
#include "Core/PairPreparation.h"

#include <juce_audio_utils/juce_audio_utils.h>

#include <array>
#include <atomic>

namespace abxapp
{
enum class Audition
{
    A = 0,
    B = 1,
    X = 2
};

class AudioPlaybackEngine final : private juce::AudioIODeviceCallback
{
public:
    AudioPlaybackEngine();
    ~AudioPlaybackEngine() override;

    [[nodiscard]] juce::String initialiseDevice();
    [[nodiscard]] bool hasAudioDevice() const;
    [[nodiscard]] double currentSampleRate();

    void prepare(abx::AudioData reference, abx::AudioData comparison);
    void clear();

    void play();
    void pause();
    [[nodiscard]] bool isPlaying() const;

    void setSource(Audition source);
    [[nodiscard]] Audition requestedSource() const;
    void setXUsesA(bool shouldUseA);

    void setSelection(abx::Frame start, abx::Frame end);
    [[nodiscard]] abx::Frame selectionStart() const;
    [[nodiscard]] abx::Frame selectionEnd() const;
    void setLooping(bool shouldLoop);
    [[nodiscard]] bool isLooping() const;

    void setPosition(abx::Frame frame);
    [[nodiscard]] abx::Frame position() const;
    [[nodiscard]] abx::Frame totalFrames() const;

private:
    void audioDeviceIOCallbackWithContext(const float* const* inputChannelData,
                                          int numInputChannels,
                                          float* const* outputChannelData,
                                          int numOutputChannels,
                                          int numSamples,
                                          const juce::AudioIODeviceCallbackContext& context) override;
    void audioDeviceAboutToStart(juce::AudioIODevice* device) override;
    void audioDeviceStopped() override;

    [[nodiscard]] float sampleAt(Audition source,
                                 abx::Frame frame,
                                 int outputChannel) const;
    [[nodiscard]] int transitionLengthFor(abx::Frame selectionLength) const;

    juce::AudioDeviceManager deviceManager_;
    bool callbackRegistered_ = false;
    bool prepared_ = false;
    std::array<abx::AudioData, 2> sources_;
    abx::Frame sourceFrames_ = 0;

    std::atomic<int> requestedSource_ {0};
    std::atomic<bool> playing_ {false};
    std::atomic<bool> looping_ {true};
    std::atomic<bool> xUsesA_ {true};
    std::atomic<bool> pendingXUsesA_ {true};
    std::atomic<bool> xMappingUpdatePending_ {false};
    std::atomic<abx::Frame> playhead_ {0};
    std::atomic<abx::Frame> selectionStart_ {0};
    std::atomic<abx::Frame> selectionEnd_ {0};

    int activeSource_ = 0;
    bool fadeActive_ = false;
    int fadeFromSource_ = 0;
    int fadeToSource_ = 0;
    abx::Frame fadeFromPosition_ = 0;
    abx::Frame fadeToPosition_ = 0;
    abx::Frame fadePosition_ = 0;
    abx::Frame fadeLength_ = 1;
    int fadeFrames_ = 480;
};
} // namespace abxapp
