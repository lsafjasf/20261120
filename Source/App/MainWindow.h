#pragma once

#include "MainComponent.h"

#include <juce_gui_extra/juce_gui_extra.h>

namespace abxapp
{
class MainWindow final : public juce::DocumentWindow
{
public:
    explicit MainWindow(std::optional<abx::AssignmentPlan> testPlan);
    ~MainWindow() override = default;

    void loadFiles(const juce::File& reference, const juce::File& comparison);
    void closeButtonPressed() override;
};
} // namespace abxapp
