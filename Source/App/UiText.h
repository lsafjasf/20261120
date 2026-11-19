#pragma once

#include <juce_core/juce_core.h>

namespace abxapp
{
inline juce::String uiText(const char* utf8)
{
    return juce::String::fromUTF8(utf8);
}
} // namespace abxapp
