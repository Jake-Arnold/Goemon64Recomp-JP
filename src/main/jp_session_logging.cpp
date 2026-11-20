// Japanese native Linux session logging: persistent, per-launch and crash-safe.
// The USA target and the pinned N64ModernRuntime submodule do not use this.
#include "jp_session_logging.h"

#include <algorithm>
#include <atomic>
#include <cerrno>
#include <csignal>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <initializer_list>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <filesystem>
#include <string>
#include <system_error>
#include <thread>
#include <vector>

#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

#if defined(__x86_64__)
#include <ucontext.h>
#endif

namespace {
namespace fs = std::filesystem;
constexpr std::size_t kLogRetention = 20;
alignas(std::max_align_t) unsigned char g_signal_stack[64 * 1024];

struct Session {
    int file = -1;
    int saved_stdout = -1;
    int saved_stderr = -1;
    int read_pipe = -1;
    std::atomic_bool exit_requested{false};
    std::thread reader;
};
Session* g_session = nullptr;
// Signal handler uses only these pre-initialized descriptors and write().
volatile sig_atomic_t g_signal_log_fd = -1;

void write_fully(int fd, const char* data, std::size_t size) noexcept {
    if (fd < 0) return;
    while (size != 0) {
        const ssize_t n = ::write(fd, data, size);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) return; // A closed terminal must never block logging.
        data += n;
        size -= static_cast<std::size_t>(n);
    }
}

void add_text(char* buffer, std::size_t cap, std::size_t& used, const char* text) noexcept {
    while (*text && used < cap) buffer[used++] = *text++;
}
void add_decimal(char* buffer, std::size_t cap, std::size_t& used, unsigned value) noexcept {
    char digits[16];
    std::size_t count = 0;
    do { digits[count++] = static_cast<char>('0' + (value % 10)); value /= 10; } while (value);
    while (count && used < cap) buffer[used++] = digits[--count];
}
void add_hex(char* buffer, std::size_t cap, std::size_t& used, uintptr_t value) noexcept {
    static constexpr char digits[] = "0123456789abcdef";
    char pending[2 * sizeof(uintptr_t)];
    std::size_t count = 0;
    do { pending[count++] = digits[value & 15U]; value >>= 4U; } while (value);
    while (count && used < cap) buffer[used++] = pending[--count];
}

// No fprintf, allocation, std::string, locks or backtrace from signal context.
void fatal_signal(int sig, siginfo_t* info, void* context) noexcept {
    char line[256];
    std::size_t used = 0;
    add_text(line, sizeof(line), used, "\n[Goemon JP FATAL SIGNAL] ");
    switch (sig) {
        case SIGSEGV: add_text(line, sizeof(line), used, "SIGSEGV"); break;
        case SIGABRT: add_text(line, sizeof(line), used, "SIGABRT"); break;
        case SIGBUS:  add_text(line, sizeof(line), used, "SIGBUS"); break;
        case SIGILL:  add_text(line, sizeof(line), used, "SIGILL"); break;
        case SIGFPE:  add_text(line, sizeof(line), used, "SIGFPE"); break;
        default: add_text(line, sizeof(line), used, "signal"); break;
    }
    add_text(line, sizeof(line), used, " number=");
    add_decimal(line, sizeof(line), used, static_cast<unsigned>(sig));
    if (info) {
        add_text(line, sizeof(line), used, " fault_address=0x");
        add_hex(line, sizeof(line), used, reinterpret_cast<uintptr_t>(info->si_addr));
    }
#if defined(__x86_64__) && defined(REG_RIP)
    if (context) {
        const auto* uc = static_cast<const ucontext_t*>(context);
        add_text(line, sizeof(line), used, " instruction_pointer=0x");
        add_hex(line, sizeof(line), used, static_cast<uintptr_t>(uc->uc_mcontext.gregs[REG_RIP]));
    }
#else
    (void)context;
#endif
    add_text(line, sizeof(line), used, " (see core dump for stack trace)\n");
    // Never write to a potentially disconnected terminal from a crash
    // handler: SIGPIPE or a blocked console could hide the real crash.
    write_fully(static_cast<int>(g_signal_log_fd), line, used);

    // Keep normal Linux core-dump and debugger behavior instead of silently
    // converting a SIGSEGV into a success or a catchable C++ exception.
    struct sigaction restore{};
    restore.sa_handler = SIG_DFL;
    sigemptyset(&restore.sa_mask);
    sigaction(sig, &restore, nullptr);
    // SIGSEGV and other synchronous signals are masked while inside their
    // handler. Unblock before re-raising to preserve the normal core dump.
    sigset_t unmask;
    sigemptyset(&unmask);
    sigaddset(&unmask, sig);
    sigprocmask(SIG_UNBLOCK, &unmask, nullptr);
    kill(getpid(), sig);
    _exit(128 + sig);
}

void install_fatal_handlers() noexcept {
    stack_t alternate{};
    alternate.ss_sp = g_signal_stack;
    alternate.ss_size = sizeof(g_signal_stack);
    if (sigaltstack(&alternate, nullptr) != 0) return;
    struct sigaction handler{};
    handler.sa_sigaction = fatal_signal;
    handler.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_RESETHAND;
    sigemptyset(&handler.sa_mask);
    for (const int sig : {SIGSEGV, SIGABRT, SIGBUS, SIGILL, SIGFPE}) {
        sigaction(sig, &handler, nullptr);
    }
}

void stop_session() noexcept {
    Session* s = g_session;
    if (!s) return;
    // The original stdout/stderr descriptors close the write ends of the
    // pipe. The reader then drains ALL ordinary-exit output before join.
    std::fflush(stdout);
    std::fflush(stderr);
    if (s->saved_stdout >= 0) ::dup2(s->saved_stdout, STDOUT_FILENO);
    else ::close(STDOUT_FILENO);
    if (s->saved_stderr >= 0) ::dup2(s->saved_stderr, STDERR_FILENO);
    else ::close(STDERR_FILENO);
    // A subprocess may inherit an extra copy of the stdout pipe writer.
    // Stop after the pipe has stayed empty for one poll interval even if
    // such an inherited descriptor has not closed yet.
    s->exit_requested.store(true, std::memory_order_release);
    if (s->reader.joinable()) s->reader.join();
    g_signal_log_fd = -1;
    if (s->saved_stdout >= 0) ::close(s->saved_stdout);
    if (s->saved_stderr >= 0) ::close(s->saved_stderr);
    ::close(s->file);
    delete s;
    g_session = nullptr;
}

void retain_recent_sessions(const fs::path& directory, const fs::path& current) noexcept {
    try {
        std::error_code ec;
        std::vector<fs::path> paths;
        for (const auto& entry : fs::directory_iterator(directory, ec)) {
            if (ec) break;
            const std::string name = entry.path().filename().string();
            if (name.starts_with("session-") && name.ends_with(".log") &&
                entry.path() != current && entry.is_regular_file(ec) && !ec) {
                paths.push_back(entry.path());
            }
        }
        // Names begin with sortable local timestamps; PID and unique suffix
        // make simultaneous launches use separate files.
        std::sort(paths.begin(), paths.end(), std::greater<>());
        // The just-opened log is never removed, even after a clock jump.
        for (std::size_t i = kLogRetention - 1; i < paths.size(); ++i)
            fs::remove(paths[i], ec);
    } catch (...) {
        // Rotation must never prevent the game from starting.
    }
}

fs::path get_directory() {
    const char* xdg = std::getenv("XDG_STATE_HOME");
    if (xdg && *xdg && fs::path(xdg).is_absolute())
        return fs::path(xdg) / "goemon64recomp-jp" / "logs";
    const char* home = std::getenv("HOME");
    if (!home || !*home || !fs::path(home).is_absolute()) return {};
    return fs::path(home) / ".local" / "state" / "goemon64recomp-jp" / "logs";
}
} // namespace

bool goemon_jp::logging::start_session(const char* version, const char* git_commit) noexcept {
    try {
        if (g_session) return true;
        // A standalone desktop launcher may have closed fd 0/1/2. Fill
        // them BEFORE opening a logfile or pipe; otherwise the log file
        // could occupy stdout and be overwritten by a later dup2 redirect.
        for (int standard : {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO}) {
            if (::fcntl(standard, F_GETFD) < 0 && errno == EBADF) {
                const int flags = (standard == STDIN_FILENO ? O_RDONLY : O_WRONLY)
                    | O_CLOEXEC;
                const int nullfd = ::open("/dev/null", flags);
                if (nullfd < 0) return false;
                if (nullfd != standard) {
                    const bool ok = ::dup2(nullfd, standard) >= 0;
                    ::close(nullfd);
                    if (!ok) return false;
                }
            }
        }
        const fs::path directory = get_directory();
        if (directory.empty()) return false;
        std::error_code ec;
        fs::create_directories(directory, ec);
        if (ec) return false;
        // Logs can contain paths and other private diagnostic information.
        ::chmod(directory.c_str(), 0700);

        std::time_t now = std::time(nullptr);
        std::tm local{};
        if (!localtime_r(&now, &local)) return false;
        char date[32]{};
        if (!std::strftime(date, sizeof(date), "%Y%m%d-%H%M%S", &local)) return false;
        std::string template_path =
            (directory / (std::string("session-") + date + "-pid" +
                          std::to_string(::getpid()) + "-XXXXXX.log")).string();
        std::vector<char> writable(template_path.begin(), template_path.end());
        writable.push_back('\0');
        int file = ::mkstemps(writable.data(), 4); // retains the .log extension
        if (file < 0) return false;
        ::fcntl(file, F_SETFD, FD_CLOEXEC);
        const int original_flags = ::fcntl(file, F_GETFL);
        if (original_flags >= 0) ::fcntl(file, F_SETFL, original_flags | O_APPEND);
        ::fchmod(file, 0600);

        int fds[2];
        if (::pipe(fds) != 0) { ::close(file); return false; }
        ::fcntl(fds[0], F_SETFD, FD_CLOEXEC);
        ::fcntl(fds[1], F_SETFD, FD_CLOEXEC);
        auto* s = new Session;
        s->file = file;
        s->saved_stdout = ::dup(STDOUT_FILENO);
        s->saved_stderr = ::dup(STDERR_FILENO);
        if (s->saved_stdout >= 0) ::fcntl(s->saved_stdout, F_SETFD, FD_CLOEXEC);
        if (s->saved_stderr >= 0) ::fcntl(s->saved_stderr, F_SETFD, FD_CLOEXEC);
        s->read_pipe = fds[0];
        // Fail before touching process descriptors if thread creation fails.
        try {
            s->reader = std::thread([s]() noexcept {
                // Tee output to the original terminal if available, but
                // never let a terminal/pipe disconnection SIGPIPE the game.
                sigset_t blocked;
                sigemptyset(&blocked);
                sigaddset(&blocked, SIGPIPE);
                pthread_sigmask(SIG_BLOCK, &blocked, nullptr);
                char buffer[8192];
                const int console = s->saved_stderr >= 0 ?
                    s->saved_stderr : s->saved_stdout;
                for (;;) {
                    pollfd ready{s->read_pipe, POLLIN, 0};
                    const int polled = ::poll(&ready, 1, 75);
                    if (polled < 0 && errno == EINTR) continue;
                    if (polled < 0) break;
                    if (polled == 0) {
                        if (s->exit_requested.load(std::memory_order_acquire)) break;
                        continue;
                    }
                    if (!(ready.revents & (POLLIN | POLLHUP))) break;
                    const ssize_t n = ::read(s->read_pipe, buffer, sizeof(buffer));
                    if (n < 0 && errno == EINTR) continue;
                    if (n <= 0) break;
                    write_fully(s->file, buffer, static_cast<std::size_t>(n));
                    write_fully(console, buffer, static_cast<std::size_t>(n));
                }
                ::close(s->read_pipe);
            });
        } catch (...) {
            ::close(fds[0]); ::close(fds[1]); ::close(file);
            if (s->saved_stdout >= 0) ::close(s->saved_stdout);
            if (s->saved_stderr >= 0) ::close(s->saved_stderr);
            delete s;
            return false;
        }
        std::fflush(stdout);
        std::fflush(stderr);
        if (::dup2(fds[1], STDOUT_FILENO) < 0 ||
            ::dup2(fds[1], STDERR_FILENO) < 0) {
            // Extremely unusual partial dup2 failure: restore both streams,
            // release the pipe writer, and join the reader.
            if (s->saved_stdout >= 0) ::dup2(s->saved_stdout, STDOUT_FILENO);
            if (s->saved_stderr >= 0) ::dup2(s->saved_stderr, STDERR_FILENO);
            ::close(fds[1]);
            s->reader.join();
            if (s->saved_stdout >= 0) ::close(s->saved_stdout);
            if (s->saved_stderr >= 0) ::close(s->saved_stderr);
            ::close(file);
            delete s;
            return false;
        }
        ::close(fds[1]);
        setvbuf(stdout, nullptr, _IONBF, 0);
        setvbuf(stderr, nullptr, _IONBF, 0);
        g_session = s;
        g_signal_log_fd = file;
        std::atexit(stop_session);
        std::at_quick_exit(stop_session);
        install_fatal_handlers();

        const fs::path current = fs::path(writable.data());
        // latest.log is an atomic symlink into the same private directory.
        // A prior session remains readable until rotation removes it.
        const fs::path temporary = directory / (".latest-" + std::to_string(::getpid()));
        fs::remove(temporary, ec);
        fs::create_symlink(current.filename(), temporary, ec);
        if (!ec) {
            fs::rename(temporary, directory / "latest.log", ec);
            if (ec) fs::remove(temporary, ec);
        }
        retain_recent_sessions(directory, current);
        std::fprintf(stderr, "[Goemon JP session log] %s\n", current.c_str());
        std::fprintf(stderr, "[Goemon JP build] version=%s commit=%s pid=%ld\n",
                     version ? version : "unknown", git_commit ? git_commit : "unknown",
                     static_cast<long>(::getpid()));
        std::fprintf(stderr,
                     "[Goemon JP session log] Persistent stdout/stderr capture active; "
                     "fatal signals write an immediate crash marker.\n");
        return true;
    } catch (...) {
        // Directory/path errors cannot prevent gameplay.
        return false;
    }
}

void goemon_jp::logging::finish_session() noexcept {
    stop_session();
}
