cmake_minimum_required(VERSION 3.16)
project(mxf_trackcheck LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(mxfcore
    src/rational.cpp
    src/timecode.cpp
    src/mxf.cpp
    src/verify.cpp
    src/fixture.cpp
)
target_include_directories(mxfcore PUBLIC include)
target_compile_options(mxfcore PRIVATE -Wall -Wextra)

add_executable(mxf-trackcheck src/main.cpp)
target_link_libraries(mxf-trackcheck PRIVATE mxfcore)

add_executable(mxf-fixture tools/make_fixture.cpp)
target_link_libraries(mxf-fixture PRIVATE mxfcore)

enable_testing()
add_executable(mxf-tests tests/test_mxf.cpp)
target_link_libraries(mxf-tests PRIVATE mxfcore)
add_test(NAME mxf-tests COMMAND mxf-tests)
