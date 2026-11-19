#include "mxf/fixture.hpp"

#include <iostream>
#include <string>

int main(int argc, char** argv) {
    const std::string path = argc > 1 ? argv[1] : "fixtures/interleaved.mxf";
    mxf::FixtureOptions options;
    std::string error;
    if (!mxf::write_sample_mxf(path, options, error)) {
        std::cerr << "mxf-fixture: " << error << "\n";
        return 1;
    }
    std::cout << "wrote " << path << ": " << options.frames
              << " interleaved edit units, " << options.edit_rate.str()
              << ", audio " << options.sample_rate << " Hz\n";
    return 0;
}
