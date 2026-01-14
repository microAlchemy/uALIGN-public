# json v3.7.3
# CMake 4.x drops compatibility with <3.5; keep this dependency configurable.
set(CMAKE_POLICY_VERSION_MINIMUM 3.5 CACHE STRING "" FORCE)
FetchContent_Declare(
    json
    GIT_REPOSITORY https://github.com/ArthurSonzogni/nlohmann_json_cmake_fetchcontent
    GIT_TAG v3.7.3
)
FetchContent_MakeAvailable(json)
