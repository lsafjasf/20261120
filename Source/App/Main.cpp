#include "MainWindow.h"
#include "UiText.h"

#include <juce_gui_extra/juce_gui_extra.h>

#include <optional>
#include <string>

namespace abxapp
{
class ABXApplication final : public juce::JUCEApplication
{
public:
    const juce::String getApplicationName() override
    {
        return "ABX Blind Workbench";
    }

    const juce::String getApplicationVersion() override
    {
        return "1.0.0";
    }

    bool moreThanOneInstanceAllowed() override
    {
        return false;
    }

    void initialise(const juce::String&) override
    {
        std::optional<abx::AssignmentPlan> testPlan;
        juce::String argumentError;
        juce::String screenshotPath;
        juce::String referencePath;
        juce::String comparisonPath;

        for (const auto& argument : getCommandLineParameterArray())
        {
            if (argument.startsWith("--test-sequence="))
            {
                const auto sequence = argument.fromFirstOccurrenceOf("=", false, false);
                const auto parsed = abx::parseReplayOrder(sequence.toStdString());
                if (parsed.has_value())
                    testPlan = abx::makeReplayPlan(*parsed);
                else
                    argumentError = uiText("测试序列必须由 A/B 或 0/1 组成，且不能为空。");
            }
            else if (argument.startsWith("--test-seed="))
            {
                const auto seedText = argument.fromFirstOccurrenceOf("=", false, false);
                try
                {
                    testPlan = abx::makeSeededPlan(
                        static_cast<std::uint64_t>(std::stoull(seedText.toStdString())));
                }
                catch (...)
                {
                    argumentError = uiText("测试种子必须是有效的无符号整数。");
                }
            }
            else if (argument.startsWith("--screenshot="))
            {
                screenshotPath = argument.fromFirstOccurrenceOf("=", false, false);
            }
            else if (argument.startsWith("--reference="))
            {
                referencePath = argument.fromFirstOccurrenceOf("=", false, false);
            }
            else if (argument.startsWith("--comparison="))
            {
                comparisonPath = argument.fromFirstOccurrenceOf("=", false, false);
            }
        }

        window_ = std::make_unique<MainWindow>(std::move(testPlan));
        window_->setVisible(true);

        if (referencePath.isNotEmpty() && comparisonPath.isNotEmpty())
        {
            window_->loadFiles(juce::File(referencePath), juce::File(comparisonPath));
        }

        if (argumentError.isNotEmpty())
        {
            juce::MessageManager::callAsync(
                [message = argumentError]
                {
                    juce::AlertWindow::showMessageBoxAsync(
                        juce::MessageBoxIconType::WarningIcon,
                        uiText("测试模式参数无效"),
                        message);
                });
        }

        if (screenshotPath.isNotEmpty())
        {
            juce::Timer::callAfterDelay(
                1200,
                [this, path = screenshotPath]
                {
                    if (window_ != nullptr)
                    {
                        if (auto* content = window_->getContentComponent())
                        {
                            const auto image = content->createComponentSnapshot(
                                content->getLocalBounds());
                            juce::File output(path);
                            output.deleteFile();
                            if (auto stream = output.createOutputStream())
                            {
                                juce::PNGImageFormat png;
                                png.writeImageToStream(image, *stream);
                            }
                        }
                    }

                    quit();
                });
        }
    }

    void shutdown() override
    {
        window_.reset();
    }

    void systemRequestedQuit() override
    {
        quit();
    }

    void anotherInstanceStarted(const juce::String&) override {}

private:
    std::unique_ptr<MainWindow> window_;
};
} // namespace abxapp

START_JUCE_APPLICATION(abxapp::ABXApplication)
