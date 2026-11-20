#pragma once

// Japanese Linux native build only. Called once from main() before preload,
// SDL, the game runtime and any guest/native workers.
namespace goemon_jp::logging {
// On failure leaves stdout/stderr untouched and returns false.
bool start_session(const char* version, const char* git_commit) noexcept;
// Restore original streams before a launcher hands them to a new process.
void finish_session() noexcept;
}
