if(POLICY CMP0207)
    cmake_policy(SET CMP0207 NEW)
endif()
if(NOT DEFINED EXECUTABLE)
    message(FATAL_ERROR "Runtime check requires an executable")
endif()
# The executable links the LLVM runtimes statically (-static), and its
# GUI.Forms DLLs were staged beside it from the SDK. Resolve imports against
# that directory alone: a DLL found nowhere else, such as libc++.dll or
# libunwind.dll from the toolchain, is reported unresolved and stops the build.
get_filename_component(destination "${EXECUTABLE}" DIRECTORY)
file(GET_RUNTIME_DEPENDENCIES
    EXECUTABLES "${EXECUTABLE}"
    DIRECTORIES "${destination}"
    RESOLVED_DEPENDENCIES_VAR dependencies
    UNRESOLVED_DEPENDENCIES_VAR unresolved
    PRE_EXCLUDE_REGEXES "api-ms-.*" "ext-ms-.*"
    POST_EXCLUDE_REGEXES ".*[/\\][Ww][Ii][Nn][Dd][Oo][Ww][Ss][/\\].*")
if(unresolved)
    message(FATAL_ERROR "Imports neither a Windows system DLL nor a staged GUI.Forms DLL: ${unresolved}")
endif()
# Windows paths compare without case; both sides are CMake (forward-slash) paths.
string(TOLOWER "${destination}" destination_key)
foreach(dependency IN LISTS dependencies)
    get_filename_component(directory "${dependency}" DIRECTORY)
    string(TOLOWER "${directory}" directory_key)
    if(NOT directory_key STREQUAL destination_key)
        message(FATAL_ERROR "Runtime dependency resolved outside the executable directory: ${dependency}")
    endif()
endforeach()
