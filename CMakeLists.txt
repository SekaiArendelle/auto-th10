cmake_minimum_required(VERSION 3.26)

project(auto_th10 VERSION 0.1.0 LANGUAGES C)

if(NOT WIN32)
    message(FATAL_ERROR "auto-th10 currently supports Windows only")
endif()

option(AUTO_TH10_BUILD_PYTHON "Build the CPython extension" ON)
option(AUTO_TH10_BUILD_TESTS "Build C tests" ON)
option(AUTO_TH10_BUILD_TOOLS "Build the th10ctl command line driver" ON)

add_library(th10_core STATIC
    src/capture.c
    src/input.c
    src/memory.c
    src/session.c
    src/snapshot.c
    src/state.c
)
target_compile_features(th10_core PUBLIC c_std_17)
target_include_directories(th10_core
    PUBLIC "$<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>"
)
target_link_libraries(th10_core PRIVATE kernel32 user32 imm32)
set_target_properties(th10_core PROPERTIES POSITION_INDEPENDENT_CODE ON)

if(MSVC)
    target_compile_options(th10_core PRIVATE /W4 /utf-8)
else()
    target_compile_options(th10_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(AUTO_TH10_BUILD_PYTHON)
    find_package(Python3 REQUIRED COMPONENTS Interpreter Development.Module)
    Python3_add_library(_native MODULE WITH_SOABI python/auto_th10/_native.c)
    target_compile_features(_native PRIVATE c_std_17)
    target_link_libraries(_native PRIVATE th10_core)
    target_include_directories(_native PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include")
    if(MSVC)
        # Official Windows Python builds use the release CRT, including when the
        # extension itself is compiled with debug information.
        set_property(TARGET _native PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
        target_compile_options(_native PRIVATE /U_DEBUG)
    endif()
    install(TARGETS _native LIBRARY DESTINATION auto_th10 RUNTIME DESTINATION auto_th10)
    install(FILES
        python/auto_th10/__init__.py
        python/auto_th10/env.py
        python/auto_th10/session.py
        python/auto_th10/types.py
        DESTINATION auto_th10
    )
endif()

if(AUTO_TH10_BUILD_TOOLS)
    add_executable(th10ctl tools/th10ctl.c)
    target_link_libraries(th10ctl PRIVATE th10_core)
    if(MSVC)
        target_compile_options(th10ctl PRIVATE /W4 /utf-8)
    else()
        target_compile_options(th10ctl PRIVATE -Wall -Wextra -Wpedantic)
    endif()
endif()

if(AUTO_TH10_BUILD_TESTS)
    enable_testing()
    add_executable(test_c_api tests/c/test_c_api.c)
    target_link_libraries(test_c_api PRIVATE th10_core)
    add_test(NAME c_api COMMAND test_c_api)
    if(AUTO_TH10_BUILD_PYTHON)
        # The tag -> exception mapping lives in static functions of the binding,
        # so this test includes the binding and starts an interpreter instead of
        # loading the module it cannot call into. It needs pythonXYY.dll on PATH,
        # which the pixi tasks provide when they run ctest.
        add_executable(test_native_mapping tests/c/test_native_mapping.c)
        target_link_libraries(test_native_mapping PRIVATE th10_core Python3::Module)
        target_include_directories(test_native_mapping
            PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/include"
        )
        if(MSVC)
            set_property(TARGET test_native_mapping PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreadedDLL")
            target_compile_options(test_native_mapping PRIVATE /U_DEBUG)
        else()
            # -Wpedantic is left out because the binding's PyType_Slot tables are
            # not written for it (ISO C forbids a function pointer in a void *
            # slot), and the extension itself is not built with it either.
            target_compile_options(test_native_mapping PRIVATE -Wall -Wextra)
        endif()
        add_test(NAME native_mapping COMMAND test_native_mapping)
    endif()
endif()
