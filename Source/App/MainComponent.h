#pragma once

#include "AudioPlaybackEngine.h"
#include "TimelineComponent.h"
#include "Core/Session.h"

#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_gui_basics/juce_gui_basics.h>

#include <atomic>
#include <memory>
#include <optional>
#include <string>

namespace abxapp
{
class MainComponent final : public juce::Component,
                            private juce::Timer
{
public:
    explicit MainComponent(std::optional<abx::AssignmentPlan> testPlan = std::nullopt);
    ~MainComponent() override;

    void loadFiles(const juce::File& reference, const juce::File& comparison);

    void paint(juce::Graphics& graphics) override;
    void resized() override;

private:
    void configureUi();
    void configureButton(juce::TextButton& button, juce::Colour accent);
    void timerCallback() override;

    void resetForNewSession();
    void chooseFile(bool forA);
    void readFile(const juce::File& file, bool forA);
    void prepareIfReady();
    void finishPreparation(abx::PreparedPair pair, std::string error);
    void startSessionWith(abx::PreparedPair pair);

    void setAudition(Audition audition);
    void submitAnswer(abx::Answer answer);
    void finishSession();
    [[nodiscard]] bool saveSession(bool final);

    void updateSelection(double startSeconds, double endSeconds, bool finished);
    void refreshLabels();
    [[nodiscard]] abx::Selection currentSelection() const;
    [[nodiscard]] std::string makeTimestamp() const;

    std::optional<abx::AssignmentPlan> testPlan_;
    abx::AssignmentPlan activePlan_;
    bool testMode_ = false;
    juce::AudioFormatManager formatManager_;
    AudioPlaybackEngine playback_;
    std::unique_ptr<juce::LookAndFeel> lookAndFeel_;

    std::optional<abx::AudioData> pendingA_;
    std::optional<abx::AudioData> pendingB_;
    std::unique_ptr<abx::AbxSession> session_;
    abx::SessionMetadata metadata_;
    abx::AlignmentResult alignment_;
    double inputASampleRate_ = 0.0;
    double inputBSampleRate_ = 0.0;
    juce::File sessionFile_;

    TimelineComponent timeline_;
    juce::Label titleLabel_;
    juce::Label subtitleLabel_;
    juce::Label loadLabel_;
    juce::Label alignmentLabel_;
    juce::Label selectionLabel_;
    juce::Label trialLabel_;
    juce::Label summaryLabel_;
    juce::Label statusLabel_;
    juce::TextButton loadAButton_ {"Load A"};
    juce::TextButton loadBButton_ {"Load B"};
    juce::TextButton aButton_ {"A"};
    juce::TextButton bButton_ {"B"};
    juce::TextButton xButton_ {"X"};
    juce::TextButton playButton_ {"Play"};
    juce::TextButton loopButton_ {"Loop"};
    juce::TextButton submitAButton_ {"X = A"};
    juce::TextButton submitBButton_ {"X = B"};
    juce::TextButton endButton_ {"End"};

    std::unique_ptr<juce::FileChooser> fileChooser_;
    std::atomic<std::uint64_t> loadGeneration_ {0};
    bool preparing_ = false;
};
} // namespace abxapp
