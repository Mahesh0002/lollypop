cmake_minimum_required(VERSION 3.16)
project(lollypop_visualizer VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# Optimization flags
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -march=native -ffast-math -flto")
set(CMAKE_CXX_FLAGS_DEBUG "-O0 -g -fsanitize=address,undefined")

# Find dependencies
find_package(Python3 REQUIRED COMPONENTS Development)
find_package(pybind11 REQUIRED)

# pybind11 (already provides CMake config)
find_package(pybind11 CONFIG REQUIRED)

# Library
add_library(visualizer_core STATIC
    src/visualizer_tick.cpp
    src/mel_remap.cpp
    src/cava_parse.cpp
    src/spectrum_normalize.cpp
    src/moving_average.cpp
    src/particle_system.cpp
    src/accent_extract.cpp
    src/pip_geometry.cpp
)

target_include_directories(visualizer_core PUBLIC
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
    $<INSTALL_INTERFACE:include>
)

target_compile_options(visualizer_core PRIVATE
    -Wall -Wextra -Wpedantic
    -Wno-unused-parameter
)

# Python module
pybind11_add_module(lollypop_visualizer python/bindings.cpp)
target_link_libraries(lollypop_visualizer PRIVATE visualizer_core)
set_target_properties(lollypop_visualizer PROPERTIES
    OUTPUT_NAME "lollypop_visualizer"
    SUFFIX "${PYTHON_MODULE_EXTENSION}"
)

# Install
install(TARGETS visualizer_core
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)
install(TARGETS lollypop_visualizer
    LIBRARY DESTINATION lib
    RUNTIME DESTINATION bin
)
install(DIRECTORY include/ DESTINATION include)

# Tests
enable_testing()
add_subdirectory(tests)