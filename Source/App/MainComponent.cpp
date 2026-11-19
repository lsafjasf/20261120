#include "MainComponent.h"

#include "Core/PairPreparation.h"
#include "BinaryData.h"
#include "UiText.h"

#include <algorithm>
#include <cmath>
#include <iomanip>
#include <limits>
#include <sstream>

namespace abxapp
{
namespace
{
class ChineseLookAndFeel final : public juce::LookAndFeel_V4
{
public:
    ChineseLookAndFeel()
        : typeface(juce::Typeface::createSystemTypefaceFor(
              BinaryData::wqymicrohei_ttc,
              static_cast<std::size_t>(BinaryData::wqymicrohei_ttcSize)))
    {
    }

    juce::Typeface::Ptr getTypefaceForFont(const juce::Font&) override
    {
        return typeface;
    }

private:
    juce::Typeface::Ptr typeface;
};

const auto background = juce::Colour(0xff0b0f14);
const auto panel = juce::Colour(0xff151b23);
const auto text = juce::Colour(0xffe6edf3);
const auto muted = juce::Colour(0xff8193a6);
const auto teal = juce::Colour(0xff4fc3f7);
const auto amber = juce::Colour(0xffffcf67);
const auto red = juce::Colour(0xffff7a7a);

juce::String secondsText(double seconds)
{
    return juce::String(seconds, 3) + " s";
}

void configureLabel(juce::Label& label,
                    float fontSize,
                    juce::Colour colour,
                    juce::Justification justification = juce::Justification::centredLeft)
{
    label.setFont(juce::Font(juce::FontOptions(fontSize)));
    label.setColour(juce::Label::textColourId, colour);
    label.setJustificationType(justification);
    label.setInterceptsMouseClicks(false, false);
}
} // namespace

MainComponent::MainComponent(std::optional<abx::AssignmentPlan> testPlan)
    : testPlan_(std::move(testPlan)),
      testMode_(testPlan_.has_value())
{
    if (testPlan_.has_value())
    {
        activePlan_ = *testPlan_;
    }
    else
    {
        const auto seed = static_cast<std::uint64_t>(
            juce::Random::getSystemRandom().nextInt64());
        activePlan_ = abx::makeSeededPlan(seed);
    }

    metadata_.testMode = testMode_;
    metadata_.replayOrder = abx::encodeReplayOrder(activePlan_.replayOrder);

    lookAndFeel_ = std::make_unique<ChineseLookAndFeel>();
    setLookAndFeel(lookAndFeel_.get());
    juce::LookAndFeel::setDefaultLookAndFeel(lookAndFeel_.get());
    formatManager_.registerBasicFormats();
    configureUi();

    const auto deviceError = playback_.initialiseDevice();
    if (deviceError.isNotEmpty() || !playback_.hasAudioDevice())
        statusLabel_.setText(uiText("未找到音频输出；对齐和记录仍可使用。"),
                             juce::dontSendNotification);
    else
        statusLabel_.setText(uiText("音频输出已就绪。"), juce::dontSendNotification);

    startTimerHz(30);
    refreshLabels();
}

MainComponent::~MainComponent()
{
    stopTimer();
    playback_.clear();
    juce::LookAndFeel::setDefaultLookAndFeel(nullptr);
    setLookAndFeel(nullptr);
}

void MainComponent::loadFiles(const juce::File& reference, const juce::File& comparison)
{
    if (session_ && !session_->hasEnded())
        return;

    resetForNewSession();
    readFile(reference, true);
    readFile(comparison, false);
}

void MainComponent::configureButton(juce::TextButton& button, juce::Colour accent)
{
    button.setColour(juce::TextButton::buttonColourId, panel);
    button.setColour(juce::TextButton::buttonOnColourId, accent.withAlpha(0.28f));
    button.setColour(juce::TextButton::textColourOffId, text);
    button.setColour(juce::TextButton::textColourOnId, juce::Colours::white);
    button.setColour(juce::ComboBox::outlineColourId, accent.withAlpha(0.45f));
    addAndMakeVisible(button);
}

void MainComponent::configureUi()
{
    configureLabel(titleLabel_, 24.0f, text);
    titleLabel_.setText(uiText("ABX 盲听工作台"), juce::dontSendNotification);
    configureLabel(subtitleLabel_, 13.5f, muted);
    subtitleLabel_.setText(
        uiText("原始母带与编码回放 · 统一采样位置 · 同区段试听"),
        juce::dontSendNotification);
    configureLabel(loadLabel_, 13.0f, muted);
    configureLabel(alignmentLabel_, 13.0f, text);
    configureLabel(selectionLabel_, 12.5f, muted);
    configureLabel(trialLabel_, 15.0f, amber);
    configureLabel(summaryLabel_, 13.5f, text);
    configureLabel(statusLabel_, 12.0f, muted);
    summaryLabel_.setJustificationType(juce::Justification::topLeft);
    statusLabel_.setJustificationType(juce::Justification::centredRight);
    summaryLabel_.setText(
        uiText("提交只记录答案与选区，不回显对错；结束会话后才显示命中率和精确二项检验。"),
        juce::dontSendNotification);

    addAndMakeVisible(titleLabel_);
    addAndMakeVisible(subtitleLabel_);
    addAndMakeVisible(loadLabel_);
    addAndMakeVisible(alignmentLabel_);
    addAndMakeVisible(selectionLabel_);
    addAndMakeVisible(trialLabel_);
    addAndMakeVisible(summaryLabel_);
    addAndMakeVisible(statusLabel_);
    addAndMakeVisible(timeline_);

    configureButton(loadAButton_, teal);
    configureButton(loadBButton_, teal);
    configureButton(aButton_, teal);
    configureButton(bButton_, amber);
    configureButton(xButton_, juce::Colour(0xffb388ff));
    configureButton(playButton_, teal);
    configureButton(loopButton_, muted);
    configureButton(submitAButton_, teal);
    configureButton(submitBButton_, amber);
    configureButton(endButton_, red);

    loadAButton_.setButtonText(uiText("载入参考 A"));
    loadBButton_.setButtonText(uiText("载入参考 B"));
    aButton_.setButtonText(uiText("A 母带"));
    bButton_.setButtonText(uiText("B 编码"));
    xButton_.setButtonText(uiText("X 未知"));
    playButton_.setButtonText(uiText("播放"));
    loopButton_.setButtonText(uiText("循环：开"));
    submitAButton_.setButtonText(uiText("提交：X = A"));
    submitBButton_.setButtonText(uiText("提交：X = B"));
    endButton_.setButtonText(uiText("结束并统计"));

    aButton_.setClickingTogglesState(true);
    bButton_.setClickingTogglesState(true);
    xButton_.setClickingTogglesState(true);
    aButton_.setRadioGroupId(1001);
    bButton_.setRadioGroupId(1001);
    xButton_.setRadioGroupId(1001);
    aButton_.setToggleState(true, juce::dontSendNotification);

    loadAButton_.onClick = [this] { chooseFile(true); };
    loadBButton_.onClick = [this] { chooseFile(false); };
    aButton_.onClick = [this] { setAudition(Audition::A); };
    bButton_.onClick = [this] { setAudition(Audition::B); };
    xButton_.onClick = [this] { setAudition(Audition::X); };
    playButton_.onClick = [this]
    {
        if (playback_.isPlaying())
            playback_.pause();
        else
            playback_.play();
    };
    loopButton_.onClick = [this]
    {
        playback_.setLooping(!playback_.isLooping());
        loopButton_.setButtonText(playback_.isLooping() ? uiText("循环：开")
                                                        : uiText("循环：关"));
    };
    submitAButton_.onClick = [this] { submitAnswer(abx::Answer::A); };
    submitBButton_.onClick = [this] { submitAnswer(abx::Answer::B); };
    endButton_.onClick = [this] { finishSession(); };
    timeline_.onSelectionChanged = [this](double start, double end, bool finished)
    {
        updateSelection(start, end, finished);
    };
}

void MainComponent::paint(juce::Graphics& graphics)
{
    juce::ColourGradient gradient(background, 0.0f, 0.0f,
                                  juce::Colour(0xff111b24),
                                  0.0f, static_cast<float>(getHeight()), false);
    graphics.setGradientFill(gradient);
    graphics.fillAll();

    graphics.setColour(juce::Colour(0x33ffffff));
    graphics.fillRect(24, 68, getWidth() - 48, 1);
    graphics.setColour(juce::Colour(0xff26313d));
    graphics.fillRoundedRectangle(
        juce::Rectangle<float>(24.0f, 82.0f,
                              static_cast<float>(getWidth() - 48), 48.0f),
        8.0f);
}

void MainComponent::resized()
{
    auto area = getLocalBounds().reduced(24);
    titleLabel_.setBounds(area.removeFromTop(32));
    subtitleLabel_.setBounds(area.removeFromTop(22));
    area.removeFromTop(14);

    auto loadRow = area.removeFromTop(34);
    loadAButton_.setBounds(loadRow.removeFromLeft(142));
    loadRow.removeFromLeft(8);
    loadBButton_.setBounds(loadRow.removeFromLeft(142));
    loadRow.removeFromLeft(12);
    loadLabel_.setBounds(loadRow);
    area.removeFromTop(14);

    alignmentLabel_.setBounds(area.removeFromTop(42));
    area.removeFromTop(6);
    timeline_.setBounds(area.removeFromTop(92));
    selectionLabel_.setBounds(area.removeFromTop(22));
    area.removeFromTop(8);

    auto auditionRow = area.removeFromTop(44);
    aButton_.setBounds(auditionRow.removeFromLeft(104));
    auditionRow.removeFromLeft(8);
    bButton_.setBounds(auditionRow.removeFromLeft(104));
    auditionRow.removeFromLeft(8);
    xButton_.setBounds(auditionRow.removeFromLeft(104));
    auditionRow.removeFromLeft(20);
    playButton_.setBounds(auditionRow.removeFromLeft(96));
    auditionRow.removeFromLeft(8);
    loopButton_.setBounds(auditionRow.removeFromLeft(104));
    area.removeFromTop(10);

    trialLabel_.setBounds(area.removeFromTop(28));
    auto submitRow = area.removeFromTop(44);
    submitAButton_.setBounds(submitRow.removeFromLeft(156));
    submitRow.removeFromLeft(8);
    submitBButton_.setBounds(submitRow.removeFromLeft(156));
    submitRow.removeFromLeft(8);
    endButton_.setBounds(submitRow.removeFromLeft(150));
    area.removeFromTop(8);

    summaryLabel_.setBounds(area.removeFromTop(86));
    statusLabel_.setBounds(getLocalBounds().reduced(24).removeFromBottom(22));
}

void MainComponent::resetForNewSession()
{
    ++loadGeneration_;
    preparing_ = false;
    session_.reset();
    pendingA_.reset();
    pendingB_.reset();
    alignment_ = {};
    inputASampleRate_ = 0.0;
    inputBSampleRate_ = 0.0;
    playback_.clear();
    timeline_.setRange(0.0);
    summaryLabel_.setText(
        uiText("提交只记录答案与选区，不回显对错；结束会话后才显示命中率和精确二项检验。"),
        juce::dontSendNotification);

    if (!testMode_)
    {
        const auto seed = static_cast<std::uint64_t>(
            juce::Random::getSystemRandom().nextInt64());
        activePlan_ = abx::makeSeededPlan(seed);
    }

    metadata_ = {};
    metadata_.testMode = testMode_;
    metadata_.replayOrder = abx::encodeReplayOrder(activePlan_.replayOrder);
}

void MainComponent::chooseFile(bool forA)
{
    if (preparing_)
        return;

    const auto callback = [safe = juce::Component::SafePointer<MainComponent>(this), forA](
                              const juce::FileChooser& chooser)
    {
        if (safe == nullptr)
            return;

        const auto file = chooser.getResult();
        if (file.existsAsFile())
            safe->readFile(file, forA);
    };

    fileChooser_ = std::make_unique<juce::FileChooser>(
        forA ? uiText("选择参考 A") : uiText("选择参考 B"),
        juce::File::getSpecialLocation(juce::File::userDocumentsDirectory),
        "*.wav;*.aif;*.aiff;*.flac;*.ogg;*.mp3",
        true);
    fileChooser_->launchAsync(
        juce::FileBrowserComponent::openMode
            | juce::FileBrowserComponent::canSelectFiles,
        callback);
}

void MainComponent::readFile(const juce::File& file, bool forA)
{
    if (session_ && !session_->hasEnded())
        return;

    if (session_ && session_->hasEnded())
        resetForNewSession();

    std::unique_ptr<juce::AudioFormatReader> reader(formatManager_.createReaderFor(file));
    if (reader == nullptr || reader->lengthInSamples <= 0
        || reader->numChannels == 0 || reader->sampleRate <= 0.0)
    {
        juce::AlertWindow::showMessageBoxAsync(
            juce::MessageBoxIconType::WarningIcon,
            uiText("无法载入"),
            uiText("该文件不是可读取的音频，或没有有效音频帧。"));
        return;
    }

    if (reader->lengthInSamples > std::numeric_limits<int>::max()
        || reader->numChannels > 32)
    {
        juce::AlertWindow::showMessageBoxAsync(
            juce::MessageBoxIconType::WarningIcon,
            uiText("无法载入"),
            uiText("文件过大或声道数过多。"));
        return;
    }

    const auto channelCount = static_cast<int>(reader->numChannels);
    const auto frameCount = static_cast<int>(reader->lengthInSamples);
    juce::AudioBuffer<float> buffer(channelCount, frameCount);
    if (!reader->read(&buffer, 0, frameCount, 0, true, true))
    {
        juce::AlertWindow::showMessageBoxAsync(
            juce::MessageBoxIconType::WarningIcon,
            uiText("无法载入"),
            uiText("读取音频数据失败。"));
        return;
    }

    abx::AudioData audio;
    audio.sampleRate = reader->sampleRate;
    audio.channels.resize(static_cast<std::size_t>(channelCount));

    for (int channel = 0; channel < channelCount; ++channel)
    {
        const auto* source = buffer.getReadPointer(channel);
        audio.channels[static_cast<std::size_t>(channel)].assign(source, source + frameCount);
    }

    if (forA)
        pendingA_ = std::move(audio);
    else
        pendingB_ = std::move(audio);

    refreshLabels();
    prepareIfReady();
}

void MainComponent::prepareIfReady()
{
    if (!pendingA_.has_value() || !pendingB_.has_value() || preparing_)
        return;

    preparing_ = true;
    const auto generation = ++loadGeneration_;
    loadAButton_.setEnabled(false);
    loadBButton_.setEnabled(false);
    statusLabel_.setText(uiText("正在统一采样率、估计延时并裁剪公共区间…"),
                         juce::dontSendNotification);

    const auto sourceA = *pendingA_;
    const auto sourceB = *pendingB_;
    auto targetRate = playback_.currentSampleRate();
    if (targetRate <= 0.0)
        targetRate = std::max(sourceA.sampleRate, sourceB.sampleRate);

    const auto safe = juce::Component::SafePointer<MainComponent>(this);
    juce::Thread::launch(
        [safe, generation, sourceA, sourceB, targetRate]() mutable
        {
            abx::PreparedPair prepared;
            std::string error;
            const bool ok = abx::preparePair(sourceA, sourceB, targetRate,
                                             prepared, error);

            juce::MessageManager::callAsync(
                [safe, generation, ok, prepared = std::move(prepared),
                 error = std::move(error)]() mutable
                {
                    if (safe == nullptr || safe->loadGeneration_.load() != generation)
                        return;

                    safe->finishPreparation(std::move(prepared), std::move(error));
                });
        });
}

void MainComponent::finishPreparation(abx::PreparedPair pair, std::string error)
{
    preparing_ = false;
    if (!error.empty())
    {
        statusLabel_.setText(uiText("准备失败。"), juce::dontSendNotification);
        juce::AlertWindow::showMessageBoxAsync(
            juce::MessageBoxIconType::WarningIcon,
            uiText("无法开始试听"),
            juce::String::fromUTF8(error.c_str()));
        refreshLabels();
        return;
    }

    startSessionWith(std::move(pair));
}

void MainComponent::startSessionWith(abx::PreparedPair pair)
{
    alignment_ = pair.alignment;
    inputASampleRate_ = pair.inputASampleRate;
    inputBSampleRate_ = pair.inputBSampleRate;

    playback_.prepare(std::move(pair.reference), std::move(pair.comparison));
    playback_.setSelection(0, playback_.totalFrames());
    playback_.setPosition(0);

    const auto duration = static_cast<double>(playback_.totalFrames())
                          / pair.workingSampleRate;
    timeline_.setRange(duration);
    timeline_.setSelection(0.0, duration);
    timeline_.setPlayhead(0.0);

    session_ = std::make_unique<abx::AbxSession>(activePlan_);
    playback_.setXUsesA(session_->secretSourceForCurrentTrial() == abx::Source::A);

    metadata_.sessionId = juce::Uuid().toString().toStdString();
    metadata_.createdAtUtc = makeTimestamp();
    metadata_.workingSampleRate = pair.workingSampleRate;
    metadata_.commonFrames = alignment_.commonFrames();
    metadata_.alignmentLagSamples = alignment_.lagSamples;
    metadata_.testMode = testMode_;
    metadata_.replayOrder = abx::encodeReplayOrder(activePlan_.replayOrder);

    auto directory = juce::File::getSpecialLocation(juce::File::userDocumentsDirectory)
                         .getChildFile("ABX Workbench");
    directory.createDirectory();
    sessionFile_ = directory.getChildFile(
        juce::String::fromUTF8(metadata_.sessionId.c_str()) + ".json");

    pendingA_.reset();
    pendingB_.reset();
    summaryLabel_.setText(
        uiText("提交只记录答案与选区，不回显对错；结束会话后才显示命中率和精确二项检验。"),
        juce::dontSendNotification);
    statusLabel_.setText(uiText("第 1 试次就绪；X 来源已隐藏。"),
                         juce::dontSendNotification);
    refreshLabels();
}

void MainComponent::setAudition(Audition audition)
{
    if (!session_ || session_->hasEnded())
        return;

    playback_.setSource(audition);
    aButton_.setToggleState(audition == Audition::A, juce::dontSendNotification);
    bButton_.setToggleState(audition == Audition::B, juce::dontSendNotification);
    xButton_.setToggleState(audition == Audition::X, juce::dontSendNotification);

    if (!playback_.isPlaying())
        playback_.play();
}

void MainComponent::submitAnswer(abx::Answer answer)
{
    if (!session_ || session_->hasEnded())
        return;

    const auto selection = currentSelection();
    if (!selection.valid())
    {
        statusLabel_.setText(uiText("请选择有效的公共区间。"),
                             juce::dontSendNotification);
        return;
    }

    if (!session_->submit(answer, selection, makeTimestamp()))
    {
        statusLabel_.setText(uiText("提交失败；请检查选区。"),
                             juce::dontSendNotification);
        return;
    }

    playback_.pause();
    playback_.setPosition(selection.alignedStart);
    playback_.setXUsesA(session_->secretSourceForCurrentTrial() == abx::Source::A);
    const bool saved = saveSession(false);

    statusLabel_.setText(
        saved ? uiText("第 ") + juce::String(session_->currentTrialNumber() - 1)
                    + uiText(" 试次已保存（未回显答案）。下一试次 X 已重新隐藏。")
              : uiText("答案已记录在内存，但记录文件写入失败。"),
        juce::dontSendNotification);
    refreshLabels();
}

void MainComponent::finishSession()
{
    if (!session_ || session_->hasEnded())
        return;

    const auto summary = session_->endSession();
    if (!summary.has_value())
    {
        statusLabel_.setText(uiText("至少提交一次后才能结束并统计。"),
                             juce::dontSendNotification);
        return;
    }

    playback_.pause();
    const bool saved = saveSession(true);

    std::ostringstream text;
    text << std::fixed << std::setprecision(1)
         << "命中率：" << summary->hits << " / " << summary->trials
         << "（" << summary->hitRate * 100.0 << "%）\n"
         << std::setprecision(4)
         << "精确二项检验（H0: p = 0.5）：单侧 p = " << summary->pOneSided
         << "；双侧 p = " << summary->pTwoSided
         << "；α = " << summary->alpha << "\n"
         << (summary->significant
                 ? "结论：高于机会水平的证据达到预设显著性。"
                 : "结论：尚未达到预设显著性。");

    summaryLabel_.setText(juce::String::fromUTF8(text.str().c_str()),
                          juce::dontSendNotification);
    statusLabel_.setText(
        saved ? uiText("会话已结束；记录与汇总已保存。")
              : uiText("会话已结束；汇总已显示，但最终记录写入失败。"),
        juce::dontSendNotification);
    refreshLabels();
}

bool MainComponent::saveSession(bool final)
{
    if (!session_ || sessionFile_ == juce::File())
        return false;

    auto directory = sessionFile_.getParentDirectory();
    if (!directory.createDirectory())
    {
        statusLabel_.setText(uiText("无法创建记录目录。"), juce::dontSendNotification);
        return false;
    }

    const auto json = final ? session_->finalJson(metadata_)
                            : session_->interimJson(metadata_);
    if (!sessionFile_.replaceWithText(juce::String::fromUTF8(json.c_str())))
    {
        statusLabel_.setText(uiText("记录写入失败。"), juce::dontSendNotification);
        return false;
    }

    return true;
}

void MainComponent::updateSelection(double startSeconds, double endSeconds, bool finished)
{
    if (!session_ || session_->hasEnded())
        return;

    const auto rate = metadata_.workingSampleRate;
    const auto totalFrames = playback_.totalFrames();
    const auto startFrame = std::clamp<abx::Frame>(
        static_cast<abx::Frame>(std::llround(startSeconds * rate)), 0, totalFrames - 1);
    const auto endFrame = std::clamp<abx::Frame>(
        static_cast<abx::Frame>(std::llround(endSeconds * rate)),
        startFrame + 1, totalFrames);
    playback_.setSelection(startFrame, endFrame);

    if (finished && !playback_.isPlaying())
        playback_.setPosition(startFrame);

    refreshLabels();
}

abx::Selection MainComponent::currentSelection() const
{
    abx::Selection selection;
    if (!session_ || metadata_.workingSampleRate <= 0.0)
        return selection;

    const auto rate = metadata_.workingSampleRate;
    const auto totalFrames = playback_.totalFrames();
    const auto startFrame = std::clamp<abx::Frame>(
        static_cast<abx::Frame>(std::llround(timeline_.getSelectionStart() * rate)),
        0, std::max<abx::Frame>(0, totalFrames - 1));
    const auto endFrame = std::clamp<abx::Frame>(
        static_cast<abx::Frame>(std::llround(timeline_.getSelectionEnd() * rate)),
        startFrame + 1, totalFrames);

    const auto toSourceFrame = [rate](abx::Frame alignedFrame, double sourceRate)
    {
        return static_cast<abx::Frame>(
            std::llround(static_cast<double>(alignedFrame) / rate * sourceRate));
    };

    selection.alignedStart = startFrame;
    selection.alignedEnd = endFrame;
    selection.sourceAStart = alignment_.commonStartA
                             + toSourceFrame(startFrame, inputASampleRate_);
    selection.sourceAEnd = alignment_.commonStartA
                           + toSourceFrame(endFrame, inputASampleRate_);
    selection.sourceBStart = alignment_.commonStartB
                             + toSourceFrame(startFrame, inputBSampleRate_);
    selection.sourceBEnd = alignment_.commonStartB
                           + toSourceFrame(endFrame, inputBSampleRate_);
    selection.sampleRate = rate;
    return selection;
}

std::string MainComponent::makeTimestamp() const
{
    return juce::Time::getCurrentTime().toISO8601(true).toStdString();
}

void MainComponent::timerCallback()
{
    if (session_ && metadata_.workingSampleRate > 0.0)
    {
        timeline_.setPlayhead(
            static_cast<double>(playback_.position()) / metadata_.workingSampleRate);
    }

    playButton_.setButtonText(playback_.isPlaying() ? uiText("暂停") : uiText("播放"));
}

void MainComponent::refreshLabels()
{
    subtitleLabel_.setText(
        testMode_ ? uiText("盲听工作台 · 测试模式（分配顺序可重放）")
                  : uiText("原始母带与编码回放 · 统一采样位置 · 同区段试听"),
        juce::dontSendNotification);

    const auto describePending = [](const std::optional<abx::AudioData>& audio,
                                    const char* name)
    {
        if (!audio.has_value())
            return juce::String(name) + uiText(" 未载入");

        return juce::String(name) + uiText(" 已载入 · ")
               + juce::String(audio->sampleRate / 1000.0, 1) + " kHz · "
               + secondsText(static_cast<double>(audio->numFrames()) / audio->sampleRate);
    };

    if (session_ && alignment_.ok)
    {
        loadLabel_.setText(
            uiText("A ") + juce::String(inputASampleRate_ / 1000.0, 1)
                + uiText(" kHz · B ") + juce::String(inputBSampleRate_ / 1000.0, 1)
                + uiText(" kHz → 工作 ")
                + juce::String(metadata_.workingSampleRate / 1000.0, 1) + " kHz",
            juce::dontSendNotification);

        const auto signedLag = (alignment_.lagSamples >= 0 ? "+" : "")
                               + juce::String(alignment_.lagSamples);
        alignmentLabel_.setText(
            uiText("对齐：B 相对 A ") + signedLag + uiText(" 样本（")
                + juce::String(alignment_.delaySeconds() * 1000.0, 3)
                + uiText(" ms）· 置信度 ")
                + juce::String(alignment_.confidence, 4) + "    "
                + uiText("有效公共区间：A ")
                + secondsText(static_cast<double>(alignment_.commonStartA)
                              / alignment_.sampleRate)
                + uiText("–")
                + secondsText(static_cast<double>(alignment_.commonEndA)
                              / alignment_.sampleRate)
                + uiText(" / B ")
                + secondsText(static_cast<double>(alignment_.commonStartB)
                              / alignment_.sampleRate)
                + uiText("–")
                + secondsText(static_cast<double>(alignment_.commonEndB)
                              / alignment_.sampleRate)
                + uiText(" / 共 ") + secondsText(alignment_.commonDurationSeconds()),
            juce::dontSendNotification);

        const auto selectionStart = timeline_.getSelectionStart();
        const auto selectionEnd = timeline_.getSelectionEnd();
        selectionLabel_.setText(
            uiText("当前选区 ") + secondsText(selectionStart) + uiText("–")
                + secondsText(selectionEnd) + uiText(" · 长度 ")
                + secondsText(selectionEnd - selectionStart),
            juce::dontSendNotification);
    }
    else
    {
        loadLabel_.setText(describePending(pendingA_, "A") + "   |   "
                               + describePending(pendingB_, "B"),
                           juce::dontSendNotification);
        alignmentLabel_.setText(
            uiText("载入两份音频后，将统一采样率、估计延时并显示有效公共区间。"),
            juce::dontSendNotification);
        selectionLabel_.setText(uiText("选区：等待载入"), juce::dontSendNotification);
    }

    if (!session_)
    {
        trialLabel_.setText(uiText("第 1 试次尚未开始。"), juce::dontSendNotification);
    }
    else if (session_->hasEnded())
    {
        trialLabel_.setText(uiText("会话已结束 · 汇总结果见下方"),
                            juce::dontSendNotification);
    }
    else
    {
        trialLabel_.setText(
            uiText("第 ") + juce::String(session_->currentTrialNumber())
                + uiText(" 试次 · X 来源已隐藏 · A / B / X 共享同一播放位置"),
            juce::dontSendNotification);
    }

    const bool activeSession = session_ && !session_->hasEnded();
    const bool canLoad = !preparing_ && (!session_ || session_->hasEnded());
    loadAButton_.setEnabled(canLoad);
    loadBButton_.setEnabled(canLoad);
    aButton_.setEnabled(activeSession);
    bButton_.setEnabled(activeSession);
    xButton_.setEnabled(activeSession);
    playButton_.setEnabled(activeSession);
    loopButton_.setEnabled(activeSession);
    submitAButton_.setEnabled(activeSession);
    submitBButton_.setEnabled(activeSession);
    endButton_.setEnabled(activeSession && session_->completedTrials() > 0);
}
} // namespace abxapp
