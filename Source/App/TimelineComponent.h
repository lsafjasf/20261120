#pragma once

#include <juce_gui_basics/juce_gui_basics.h>

#include <functional>

namespace abxapp
{
class TimelineComponent final : public juce::Component
{
public:
    TimelineComponent();

    void setRange(double durationSeconds);
    void setSelection(double startSeconds, double endSeconds);
    void setPlayhead(double seconds);

    [[nodiscard]] double getSelectionStart() const noexcept;
    [[nodiscard]] double getSelectionEnd() const noexcept;

    std::function<void(double, double, bool)> onSelectionChanged;

    void paint(juce::Graphics& graphics) override;
    void mouseDown(const juce::MouseEvent& event) override;
    void mouseDrag(const juce::MouseEvent& event) override;
    void mouseUp(const juce::MouseEvent& event) override;

private:
    [[nodiscard]] juce::Rectangle<float> trackBounds() const;
    [[nodiscard]] double timeAt(float x) const;
    [[nodiscard]] float xAt(double seconds) const;
    void notify(bool finished);

    double duration_ = 0.0;
    double selectionStart_ = 0.0;
    double selectionEnd_ = 0.0;
    double playhead_ = 0.0;
    double dragAnchor_ = 0.0;
    int dragMode_ = 0;
};
} // namespace abxapp
