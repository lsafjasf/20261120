#include "MainWindow.h"
#include "UiText.h"

namespace abxapp
{
MainWindow::MainWindow(std::optional<abx::AssignmentPlan> testPlan)
    : DocumentWindow(
          testPlan.has_value() ? uiText("ABX 盲听工作台 · 测试模式")
                               : uiText("ABX 盲听工作台"),
          juce::Colour(0xff0b0f14),
          DocumentWindow::allButtons)
{
    setUsingNativeTitleBar(true);
    setContentOwned(new MainComponent(std::move(testPlan)), true);
    setResizable(true, false);
    setResizeLimits(860, 650, 1600, 1200);
    centreWithSize(1040, 760);
    setVisible(true);
}

void MainWindow::closeButtonPressed()
{
    juce::JUCEApplication::getInstance()->systemRequestedQuit();
}

void MainWindow::loadFiles(const juce::File& reference, const juce::File& comparison)
{
    if (auto* content = dynamic_cast<MainComponent*>(getContentComponent()))
        content->loadFiles(reference, comparison);
}
} // namespace abxapp
