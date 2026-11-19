cmake_minimum_required(VERSION 3.22)

project(ABXWorkbench VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

option(ABX_BUILD_APP "Build the JUCE desktop application" ON)
option(ABX_BUILD_TESTS "Build the dependency-free core tests" ON)
option(ABX_FETCH_JUCE "Fetch JUCE when it is not already available" ON)

add_library(abx_core STATIC
    Source/Core/Alignment.cpp
    Source/Core/Assignment.cpp
    Source/Core/PairPreparation.cpp
    Source/Core/Resampler.cpp
    Source/Core/Session.cpp
    Source/Core/Statistics.cpp
    Source/Core/WavIO.cpp
)
target_include_directories(abx_core PUBLIC Source)
target_compile_features(abx_core PUBLIC cxx_std_20)
if (MSVC)
    target_compile_options(abx_core PRIVATE /W4)
else()
    target_compile_options(abx_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if (ABX_BUILD_TESTS)
    enable_testing()
    find_package(Python3 COMPONENTS Interpreter QUIET)
    add_executable(abx_core_tests tests/CoreTests.cpp)
    target_link_libraries(abx_core_tests PRIVATE abx_core)
    target_compile_definitions(abx_core_tests PRIVATE
        ABX_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
    add_test(NAME abx_core_tests COMMAND abx_core_tests)
    if (Python3_Interpreter_FOUND)
        add_test(NAME abx_ui_privacy_checks
            COMMAND ${Python3_EXECUTABLE}
                    ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_ui_privacy.py
                    ${CMAKE_CURRENT_SOURCE_DIR})
    endif()
endif()

if (ABX_BUILD_APP)
    if (EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/JUCE/CMakeLists.txt")
        add_subdirectory(JUCE)
    else()
        find_package(JUCE 8 CONFIG QUIET)
        if (NOT JUCE_FOUND AND ABX_FETCH_JUCE)
            include(FetchContent)
            FetchContent_Declare(JUCE
                GIT_REPOSITORY https://github.com/juce-framework/JUCE.git
                GIT_TAG 8.0.8
                GIT_SHALLOW TRUE)
            FetchContent_MakeAvailable(JUCE)
        endif()
    endif()

    if (NOT COMMAND juce_add_gui_app)
        message(FATAL_ERROR
            "JUCE was not found. Put a JUCE checkout at ${CMAKE_CURRENT_SOURCE_DIR}/JUCE, "
            "install a JUCE CMake package, or configure with -DABX_BUILD_APP=OFF.")
    endif()

    juce_add_gui_app(ABXWorkbench
        PRODUCT_NAME "ABX Blind Workbench"
        COMPANY_NAME "Listening Review"
        BUNDLE_ID "local.abx.workbench"
        VERSION ${PROJECT_VERSION})

    juce_add_binary_data(ABXResources SOURCES
        Resources/wqy-microhei.ttc)

    target_sources(ABXWorkbench PRIVATE
        Source/App/AudioPlaybackEngine.cpp
        Source/App/Main.cpp
        Source/App/MainComponent.cpp
        Source/App/MainWindow.cpp
        Source/App/TimelineComponent.cpp)

    target_link_libraries(ABXWorkbench PRIVATE
        abx_core
        ABXResources
        juce::juce_audio_basics
        juce::juce_audio_devices
        juce::juce_audio_formats
        juce::juce_audio_utils
        juce::juce_core
        juce::juce_data_structures
        juce::juce_events
        juce::juce_graphics
        juce::juce_gui_basics
        juce::juce_gui_extra)

    target_compile_definitions(ABXWorkbench PRIVATE
        JUCE_WEB_BROWSER=0
        JUCE_USE_CURL=0
        JUCE_APPLICATION_NAME_STRING="ABX Blind Workbench"
        JUCE_APPLICATION_VERSION_STRING="${PROJECT_VERSION}")

    if (UNIX AND NOT APPLE)
        target_link_libraries(ABXWorkbench PRIVATE
            ${CMAKE_DL_LIBS}
            pthread)
    endif()
endif()
