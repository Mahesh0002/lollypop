cmake_minimum_required(VERSION 3.16)

find_package(GTest REQUIRED)

add_executable(test_visualizer test_visualizer.cpp)
target_link_libraries(test_visualizer PRIVATE
    visualizer_core
    GTest::gtest
    GTest::gtest_main
)

target_include_directories(test_visualizer PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../include
)

add_test(NAME visualizer_tests COMMAND test_visualizer)