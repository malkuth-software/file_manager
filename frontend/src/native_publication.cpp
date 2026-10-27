#if defined(__linux__) && !defined(_GNU_SOURCE)
#define _GNU_SOURCE
#endif
#if defined(__APPLE__) && !defined(_DARWIN_C_SOURCE)
#define _DARWIN_C_SOURCE
#endif

#include "native_publication.hpp"

#include <cerrno>

#if defined(_WIN32)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#elif defined(__APPLE__)
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
#elif defined(__linux__)
#include <fcntl.h>
#include <stdio.h>
#endif

namespace file_manager {

#if defined(__APPLE__)
namespace {
// Owns one descriptor for the length of a publication; closes it on every exit.
class OwnedDescriptor final {
public:
    explicit OwnedDescriptor(const int descriptor) noexcept : value(descriptor) {}
    OwnedDescriptor(const OwnedDescriptor&) = delete;
    OwnedDescriptor& operator=(const OwnedDescriptor&) = delete;
    ~OwnedDescriptor() {
        if (value >= 0) ::close(value);
    }
    const int value;
};

std::error_code errno_error(const int native_error) noexcept {
    const std::error_code failure(native_error, std::generic_category());
    return failure;
}
} // namespace
#endif

std::error_code check_destination_vacant(const std::filesystem::path& destination) {
#if defined(_WIN32)
    const DWORD attributes = GetFileAttributesW(destination.c_str());
    if (attributes != INVALID_FILE_ATTRIBUTES) {
        const std::error_code occupied = std::make_error_code(std::errc::file_exists);
        return occupied;
    }
    const DWORD native_error = GetLastError();
    if (native_error == ERROR_FILE_NOT_FOUND) return {};
    const std::error_code failure(static_cast<int>(native_error), std::system_category());
    return failure;
#else
    std::error_code failure{};
    const std::filesystem::file_status status = std::filesystem::symlink_status(destination, failure);
    if (status.type() == std::filesystem::file_type::not_found &&
        (!failure || failure == std::errc::no_such_file_or_directory)) return {};
    if (failure) return failure;
    const std::error_code occupied = std::make_error_code(std::errc::file_exists);
    return occupied;
#endif
}

std::error_code rename_no_replace(const std::filesystem::path& source,
                                  const std::filesystem::path& destination) {
#if defined(_WIN32)
    // Zero flags: no replacement, cross-volume copy, deferred move or elevation.
    const BOOL moved = MoveFileExW(source.c_str(), destination.c_str(), 0U);
    if (moved != FALSE) return {};
    const DWORD native_error = GetLastError();
    if (native_error == ERROR_FILE_EXISTS || native_error == ERROR_ALREADY_EXISTS) {
        const std::error_code occupied = std::make_error_code(std::errc::file_exists);
        return occupied;
    }
    if (native_error == ERROR_NOT_SAME_DEVICE) {
        const std::error_code cross_volume = std::make_error_code(std::errc::cross_device_link);
        return cross_volume;
    }
    if (native_error == ERROR_NOT_SUPPORTED || native_error == ERROR_INVALID_FUNCTION) {
        const std::error_code unsupported = std::make_error_code(std::errc::operation_not_supported);
        return unsupported;
    }
    const std::error_code failure(static_cast<int>(native_error), std::system_category());
    return failure;
#elif defined(__APPLE__) || defined(__linux__)
#if defined(__APPLE__)
    const int status = ::renamex_np(source.c_str(), destination.c_str(), RENAME_EXCL);
#else
    const int status = ::renameat2(AT_FDCWD, source.c_str(), AT_FDCWD,
                                   destination.c_str(), RENAME_NOREPLACE);
#endif
    if (status == 0) return {};
    const int native_error = errno;
    const std::error_code failure(native_error, std::generic_category());
    return failure;
#else
    static_cast<void>(source);
    static_cast<void>(destination);
    const std::error_code unsupported = std::make_error_code(std::errc::operation_not_supported);
    return unsupported;
#endif
}

StagePublication publish_stage_no_replace(const std::filesystem::path& stage,
                                          const std::filesystem::path& destination) {
    StagePublication publication{};
#if defined(__APPLE__)
    struct stat observed{};
    if (::lstat(stage.c_str(), &observed) != 0) {
        publication.error = errno_error(errno);
        return publication;
    }
    const bool restricted_directory = S_ISDIR(observed.st_mode) && (observed.st_mode & S_IWUSR) == 0;
    if (!restricted_directory) {
        publication.error = rename_no_replace(stage, destination);
        publication.published = !publication.error;
        return publication;
    }
    // Hold the staged directory itself: the mode changes below act on this
    // object, never on whatever a path names later.
    const OwnedDescriptor directory(::open(stage.c_str(), O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC));
    if (directory.value < 0) {
        publication.error = errno_error(errno);
        return publication;
    }
    struct stat opened{};
    if (::fstat(directory.value, &opened) != 0) {
        publication.error = errno_error(errno);
        return publication;
    }
    if (opened.st_dev != observed.st_dev || opened.st_ino != observed.st_ino) {
        publication.error = std::make_error_code(std::errc::device_or_resource_busy);
        return publication;
    }
    const mode_t final_mode = opened.st_mode & 07777;
    if (::fchmod(directory.value, final_mode | S_IWUSR) != 0) {
        publication.error = errno_error(errno);
        return publication;
    }
    const std::error_code renamed = rename_no_replace(stage, destination);
    // Restore the exact mode whether or not the rename happened: on failure
    // the stage keeps the mode its metadata pass gave it.
    const int restored = ::fchmod(directory.value, final_mode);
    const int restore_error = restored == 0 ? 0 : errno;
    publication.published = !renamed;
    if (renamed) {
        publication.error = renamed;
        return publication;
    }
    if (restore_error != 0) publication.error = errno_error(restore_error);
    return publication;
#else
    publication.error = rename_no_replace(stage, destination);
    publication.published = !publication.error;
    return publication;
#endif
}

} // namespace file_manager
