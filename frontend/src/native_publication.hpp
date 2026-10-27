#pragma once

#include <filesystem>
#include <system_error>

namespace file_manager {

// Private synchronous adapters. Paths are borrowed only for each call.
// A successful preflight is an observation, never a reservation or authority.
// Vacancy returns no error, an existing leaf returns file_exists, and an
// unobservable leaf returns its error. Symlink leaves count as occupied.
[[nodiscard]] std::error_code check_destination_vacant(
    const std::filesystem::path& destination);

// Publish a same-volume rename without replacing any existing destination.
// No replace/copy fallback. Unsupported kernels/filesystems fail explicitly.
// The service separately owns source identity and parent-route validation;
// this operation does not make those earlier observations race-free.
[[nodiscard]] std::error_code rename_no_replace(
    const std::filesystem::path& source,
    const std::filesystem::path& destination);

// The outcome of publishing a staged object. `published` says whether the
// destination now names the staged object; `error` is the first failure. An
// error with `published` set means the object was published but its final
// mode could not be restored.
struct StagePublication final {
    std::error_code error{};
    bool published{};
};

// Publish a stage this service created, as rename_no_replace does. macOS
// refuses to rename a directory its owner cannot write, even within one
// parent (MEASURED: CI run 40466042854, macos-15). There, a staged directory
// whose final mode withholds owner write gains owner write for the rename
// only; its exact mode is then restored through a descriptor held on the
// staged directory, which still names it after the rename. Elsewhere, and for
// any other object, this is rename_no_replace. Never pass a user's object:
// this briefly changes the staged directory's mode.
[[nodiscard]] StagePublication publish_stage_no_replace(
    const std::filesystem::path& stage,
    const std::filesystem::path& destination);

} // namespace file_manager
