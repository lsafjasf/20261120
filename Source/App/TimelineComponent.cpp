#include "TimelineComponent.h"

#include <algorithm>
#include <cmath>

namespace abxapp
{
TimelineComponent::TimelineComponent()
{
    setMouseCursor(juce::MouseCursor::IBeamCursor);
    setRepaintsOnMouseActivity(true);
}

void TimelineComponent::setRange(double durationSeconds)
{
    duration_ = std::max(0.0, durationSeconds);
    selectionStart_ = std::clamp(selectionStart_, 0.0, duration_);
    selectionEnd_ = std::clamp(selectionEnd_, selectionStart_, duration_);
    repaint();
}

void TimelineComponent::setSelection(double startSeconds, double endSeconds)
{
    const auto minimum = std::min(0.10, duration_);
    auto start = std::clamp(startSeconds, 0.0, duration_);
    auto end = std::clamp(endSeconds, 0.0, duration_);
    if (end < start)
        std::swap(start, end);
    if (end - start < minimum)
        end = std::min(duration_, start + minimum);
    if (end - start < minimum)
        start = std::max(0.0, end - minimum);

    selectionStart_ = start;
    selectionEnd_ = end;
    repaint();
}

void TimelineComponent::setPlayhead(double seconds)
{
    playhead_ = std::clamp(seconds, 0.0, duration_);
    repaint();
}

double TimelineComponent::getSelectionStart() const noexcept
{
    return selectionStart_;
}

double TimelineComponent::getSelectionEnd() const noexcept
{
    return selectionEnd_;
}

juce::Rectangle<float> TimelineComponent::trackBounds() const
{
    return getLocalBounds().toFloat().reduced(14.0f, 16.0f);
}

double TimelineComponent::timeAt(float x) const
{
    const auto bounds = trackBounds();
    if (duration_ <= 0.0 || bounds.getWidth() <= 0.0f)
        return 0.0;

    return std::clamp(
        static_cast<double>((x - bounds.getX()) / bounds.getWidth()) * duration_,
        0.0, duration_);
}

float TimelineComponent::xAt(double seconds) const
{
    const auto bounds = trackBounds();
    if (duration_ <= 0.0)
        return bounds.getX();

    return bounds.getX()
           + static_cast<float>(std::clamp(seconds / duration_, 0.0, 1.0))
                 * bounds.getWidth();
}

void TimelineComponent::paint(juce::Graphics& graphics)
{
    using namespace juce;

    graphics.fillAll(Colour(0xff11151c));
    const auto bounds = trackBounds();
    const auto rounded = bounds.withHeight(24.0f).withY(bounds.getCentreY() - 12.0f);

    graphics.setColour(Colour(0xff26313d));
    graphics.fillRoundedRectangle(rounded, 6.0f);

    if (duration_ <= 0.0)
        return;

    graphics.setColour(Colour(0xff35546a));
    graphics.fillRoundedRectangle(rounded, 6.0f);

    const auto selectedLeft = xAt(selectionStart_);
    const auto selectedRight = xAt(selectionEnd_);
    graphics.setColour(Colour(0xff4fc3f7));
    graphics.fillRoundedRectangle(
        Rectangle<float>(selectedLeft, rounded.getY(),
                         std::max(1.0f, selectedRight - selectedLeft), rounded.getHeight()),
        4.0f);

    graphics.setColour(Colour(0xffbfe9ff));
    graphics.fillRect(selectedLeft - 1.5f, rounded.getY() - 3.0f, 3.0f,
                      rounded.getHeight() + 6.0f);
    graphics.fillRect(selectedRight - 1.5f, rounded.getY() - 3.0f, 3.0f,
                      rounded.getHeight() + 6.0f);

    graphics.setFont(Font(FontOptions(11.0f)));
    for (int tick = 0; tick <= 8; ++tick)
    {
        const auto time = duration_ * static_cast<double>(tick) / 8.0;
        const auto x = xAt(time);
        graphics.setColour(Colour(0x66ffffff));
        graphics.drawVerticalLine(roundToInt(x),
                                  rounded.getBottom() + 3.0f,
                                  rounded.getBottom() + 8.0f);
        graphics.setColour(Colour(0xff8193a6));

        const auto text = String(time, 1) + " s";

        graphics.drawText(text,
                          roundToInt(x) - 24, roundToInt(rounded.getBottom()) + 10,
                          48, 18, Justification::centred);
    }

    const auto playheadX = xAt(playhead_);
    graphics.setColour(Colour(0xffffcf67));
    graphics.fillRect(playheadX - 1.0f, rounded.getY() - 8.0f, 2.0f,
                      rounded.getHeight() + 16.0f);
    Path marker;
    marker.addTriangle(playheadX - 5.0f, rounded.getY() - 9.0f,
                       playheadX + 5.0f, rounded.getY() - 9.0f,
                       playheadX, rounded.getY() - 2.0f);
    graphics.fillPath(marker);
}

void TimelineComponent::mouseDown(const juce::MouseEvent& event)
{
    if (duration_ <= 0.0)
        return;

    const auto x = event.position.x;
    if (std::abs(x - xAt(selectionStart_)) <= 9.0f)
        dragMode_ = 1;
    else if (std::abs(x - xAt(selectionEnd_)) <= 9.0f)
        dragMode_ = 2;
    else
    {
        dragMode_ = 3;
        dragAnchor_ = timeAt(x);
    }
}

void TimelineComponent::mouseDrag(const juce::MouseEvent& event)
{
    if (duration_ <= 0.0 || dragMode_ == 0)
        return;

    const auto time = timeAt(event.position.x);
    const auto minimum = std::min(0.10, duration_);

    if (dragMode_ == 1)
    {
        setSelection(std::min(time, selectionEnd_ - minimum), selectionEnd_);
    }
    else if (dragMode_ == 2)
    {
        setSelection(selectionStart_, std::max(time, selectionStart_ + minimum));
    }
    else
    {
        auto start = std::min(dragAnchor_, time);
        auto end = std::max(dragAnchor_, time);
        if (end - start < minimum)
        {
            end = std::min(duration_, start + minimum);
            start = std::max(0.0, end - minimum);
        }
        setSelection(start, end);
    }

    notify(false);
}

void TimelineComponent::mouseUp(const juce::MouseEvent&)
{
    if (dragMode_ != 0)
        notify(true);

    dragMode_ = 0;
}

void TimelineComponent::notify(bool finished)
{
    if (onSelectionChanged)
        onSelectionChanged(selectionStart_, selectionEnd_, finished);
}
} // namespace abxapp
