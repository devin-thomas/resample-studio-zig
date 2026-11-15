#define _POSIX_C_SOURCE 200809L
#define _XOPEN_SOURCE 700
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <signal.h>
#include <sys/select.h>
#include <time.h>
#include <spawn.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <unistd.h>

extern char **environ;
static volatile sig_atomic_t cancelled;
static const char usage[] =
    "Usage: resample [INPUT ...] [-o|--output OUTPUT.wav|OUTPUT.mp3]\n"
    "                [--speed PERCENT | --cents CENTS] [--overwrite] [-- INPUT ...]\n"
    "Inputs concatenate in order; pitch and speed change together.\n"
    "Speed: 25..400%; cents: -2400..2400; default: original speed.\n"
    "Output: 48000 Hz stereo, 24-bit WAV or 320 kbps MP3.\n"
    "Requires FFmpeg on PATH. -h/--help, --version.\n";

typedef struct { char *name; int fd; struct stat st; } Input;
typedef struct {
    Input *inputs;
    size_t count;
    const char *output;
    double ratio;
    bool overwrite, wav;
    int action;
} Options;

static int errorf(const char *fmt, ...) {
    va_list ap;
    fputs("resample: ", stderr);
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    return -1;
}

static void *allocate(size_t n) {
    void *p = calloc(1, n);
    if (!p) { errorf("out of memory"); exit(1); }
    return p;
}

static char *format(const char *fmt, ...) {
    va_list ap, copy;
    va_start(ap, fmt);
    va_copy(copy, ap);
    int n = vsnprintf(NULL, 0, fmt, copy);
    va_end(copy);
    if (n < 0) { va_end(ap); errorf("cannot format command"); exit(1); }
    char *p = allocate((size_t)n + 1);
    vsnprintf(p, (size_t)n + 1, fmt, ap);
    va_end(ap);
    return p;
}

/* strtod alone accepts hexadecimal, whitespace, NaN and infinity. */
static bool decimal(const char *s, double *value) {
    const char *p = s;
    if (*p == '+' || *p == '-') ++p;
    bool digit = false;
    while (*p >= '0' && *p <= '9') { digit = true; ++p; }
    if (*p == '.') {
        ++p;
        while (*p >= '0' && *p <= '9') { digit = true; ++p; }
    }
    if (!digit) return false;
    if (*p == 'e' || *p == 'E') {
        ++p;
        if (*p == '+' || *p == '-') ++p;
        const char *start = p;
        while (*p >= '0' && *p <= '9') ++p;
        if (p == start) return false;
    }
    if (*p) return false;
    errno = 0;
    char *end;
    *value = strtod(s, &end);
    return !*end && isfinite(*value) && errno != ERANGE;
}

static int parse(int argc, char **argv, Options *o) {
    o->ratio = 1;
    o->inputs = allocate((size_t)argc * sizeof(*o->inputs));
    if (argc == 1) o->action = 1;
    bool literal = false, rate_set = false;
    for (int i = 1; i < argc; ++i) {
        const char *arg = argv[i];
        if (!literal && !strcmp(arg, "--")) literal = true;
        else if (!literal && (!strcmp(arg, "-h") || !strcmp(arg, "--help"))) o->action = 1;
        else if (!literal && !strcmp(arg, "--version")) o->action = 2;
        else if (!literal && !strcmp(arg, "--overwrite")) o->overwrite = true;
        else if (!literal && (!strcmp(arg, "-o") || !strcmp(arg, "--output"))) {
            if (o->output) return errorf("specify output only once");
            if (++i == argc || !*argv[i]) return errorf("missing output filename");
            o->output = argv[i];
        } else if (!literal && (!strcmp(arg, "--speed") || !strcmp(arg, "--cents"))) {
            if (rate_set) return errorf("choose --speed or --cents, once");
            rate_set = true;
            bool cents = !strcmp(arg, "--cents");
            double v;
            if (++i == argc || !decimal(argv[i], &v) ||
                (cents ? (v < -2400 || v > 2400) : (v < 25 || v > 400)))
                return errorf("use finite decimal --speed 25..400 or --cents -2400..2400");
            o->ratio = cents ? exp2(v / 1200) : v / 100;
        } else if (!literal && *arg == '-') return errorf("unknown option '%s'; use -- before dash filenames", arg);
        else {
            o->inputs[o->count].name = argv[i];
            o->inputs[o->count++].fd = -1;
        }
    }
    if (o->action) return 0;
    if (!o->count) return errorf("provide at least one input file");
    if (!o->output) return errorf("provide -o OUTPUT.wav or OUTPUT.mp3");
    const char *dot = strrchr(o->output, '.');
    o->wav = dot && !strcasecmp(dot, ".wav");
    if (!o->wav && (!dot || strcasecmp(dot, ".mp3"))) return errorf("output extension must be .wav or .mp3");
    return 0;
}

/* The handler never targets a PID: the event loop owns the unreaped child. */
static void on_signal(int sig) {
    if (!cancelled) cancelled = sig;
}

static void on_child(int sig) { (void)sig; }

static int install_signals(void) {
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGINT, &sa, NULL) || sigaction(SIGTERM, &sa, NULL) || sigaction(SIGHUP, &sa, NULL))
        return errorf("cannot install cancellation handlers: %s", strerror(errno));
    sa.sa_handler = SIG_IGN;
    if (sigaction(SIGPIPE, &sa, NULL))
        return errorf("cannot protect diagnostic writes: %s", strerror(errno));
    sa.sa_handler = on_child;
    if (sigaction(SIGCHLD, &sa, NULL))
        return errorf("cannot install child-exit handler: %s", strerror(errno));
    return 0;
}

static void cancel_set(sigset_t *set) {
    sigemptyset(set);
    sigaddset(set, SIGINT);
    sigaddset(set, SIGTERM);
    sigaddset(set, SIGHUP);
}

static int64_t monotonic_ms(void) {
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t)) return -1;
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

enum { DIAGNOSTIC_LIMIT = 8192 };
typedef struct {
    char bytes[DIAGNOSTIC_LIMIT];
    size_t used;
    bool truncated;
} Diagnostics;

/* Drain at most 64 KiB per event-loop pass, so noisy children cannot starve
 * cancellation or reaping. Retain only the final 8 KiB, including on failure. */
static int drain_diagnostics(int fd, Diagnostics *d) {
    for (int pass = 0; pass < 16; ++pass) {
        char chunk[4096];
        ssize_t got = read(fd, chunk, sizeof(chunk));
        if (!got) return 1;
        if (got < 0) {
            if (errno == EINTR) continue;
            if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
            return -1;
        }
        size_t n = (size_t)got;
        if (n + d->used > sizeof(d->bytes)) {
            size_t discard = n + d->used - sizeof(d->bytes);
            memmove(d->bytes, d->bytes + discard, d->used - discard);
            d->used -= discard;
            d->truncated = true;
        }
        memcpy(d->bytes + d->used, chunk, n);
        d->used += n;
    }
    return 0;
}

/* All audio I/O is seekable/streaming. No shell or whole-output buffering.
 * Validation duplicates only its selected input, avoiding O(N^2) fd actions. */
static int run_child(char **args, const Options *o, int outputfd,
                     bool validation, size_t index, int base) {
    int pipefd[2] = {-1, -1};
    pid_t pid = 0;
    int nullfd = open("/dev/null", O_RDWR | O_CLOEXEC);
    if (nullfd < 0) return errorf("cannot open /dev/null: %s", strerror(errno));
    int rc = 0;
    if (pipe(pipefd) || fcntl(pipefd[0], F_SETFD, FD_CLOEXEC) < 0 ||
        fcntl(pipefd[1], F_SETFD, FD_CLOEXEC) < 0 ||
        fcntl(pipefd[0], F_SETFL, O_NONBLOCK) < 0) {
        rc = errno;
        goto early;
    }
    if (pipefd[0] >= FD_SETSIZE) { rc = EMFILE; goto early; }
    posix_spawn_file_actions_t fa;
    posix_spawnattr_t attr;
    rc = posix_spawn_file_actions_init(&fa);
    if (rc) goto early;
    rc = posix_spawnattr_init(&attr);
    if (rc) { posix_spawn_file_actions_destroy(&fa); goto early; }
#define ACTION(call) do { if ((rc = (call))) goto actions_done; } while (0)
    ACTION(posix_spawn_file_actions_adddup2(&fa, nullfd, STDIN_FILENO));
    ACTION(posix_spawn_file_actions_adddup2(&fa, validation ? nullfd : outputfd, STDOUT_FILENO));
    ACTION(posix_spawn_file_actions_adddup2(&fa, pipefd[1], STDERR_FILENO));
    if (validation) ACTION(posix_spawn_file_actions_adddup2(&fa, o->inputs[index].fd, base));
    else for (size_t i = 0; i < o->count; ++i)
        ACTION(posix_spawn_file_actions_adddup2(&fa, o->inputs[i].fd, base + (int)i));
    sigset_t empty, defaults;
    sigemptyset(&empty);
    cancel_set(&defaults);
    sigaddset(&defaults, SIGPIPE);
    ACTION(posix_spawnattr_setsigmask(&attr, &empty));
    ACTION(posix_spawnattr_setsigdefault(&attr, &defaults));
    ACTION(posix_spawnattr_setpgroup(&attr, 0));
    ACTION(posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF | POSIX_SPAWN_SETPGROUP));
    if (cancelled) rc = EINTR;
    else rc = posix_spawnp(&pid, args[0], &fa, &attr, args, environ);
actions_done:
    posix_spawnattr_destroy(&attr);
    posix_spawn_file_actions_destroy(&fa);
#undef ACTION
early:
    close(nullfd);
    if (pipefd[1] >= 0) close(pipefd[1]);
    if (rc || pid <= 0) {
        if (!rc) rc = EIO;
        if (pipefd[0] >= 0) close(pipefd[0]);
        if (rc == ENOENT) return errorf("install FFmpeg and add it to PATH: %s not found", args[0]);
        return errorf("cannot start %s: %s", args[0], strerror(rc));
    }
    /* SIGCHLD wakes pselect without periodic polling/startup latency. Blocking
     * event signals around waitpid + pselect prevents a lost-wakeup race. */
    sigset_t blocked, previous, waiting;
    cancel_set(&blocked);
    sigaddset(&blocked, SIGCHLD);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous)) {
        int saved = errno;
        kill(-pid, SIGKILL);
        while (waitpid(pid, NULL, 0) < 0 && errno == EINTR) {}
        close(pipefd[0]);
        return errorf("cannot protect child wait: %s", strerror(saved));
    }
    waiting = previous;
    sigdelset(&waiting, SIGCHLD);
    sigdelset(&waiting, SIGINT);
    sigdelset(&waiting, SIGTERM);
    sigdelset(&waiting, SIGHUP);
    Diagnostics diagnostic = {0};
    bool stopping = false, killed = false, io_error = false;
    int status = 0;
    int64_t deadline = 0;
    for (;;) {
        if ((cancelled || io_error) && !stopping) {
            kill(-pid, cancelled ? (int)cancelled : SIGTERM);
            stopping = true;
            deadline = monotonic_ms() + 1000;
        }
        int64_t now = stopping ? monotonic_ms() : 0;
        if (stopping && !killed && (now < 0 || now >= deadline)) {
            kill(-pid, SIGKILL);
            killed = true;
        }
        if (pipefd[0] >= 0) {
            int drained = drain_diagnostics(pipefd[0], &diagnostic);
            if (drained) {
                if (drained < 0) io_error = true;
                close(pipefd[0]);
                pipefd[0] = -1;
            }
        }
        siginfo_t exited = {0};
        int wait_rc = waitid(P_PID, (id_t)pid, &exited, WEXITED | WNOHANG | WNOWAIT);
        if (!wait_rc && exited.si_pid == pid) {
            /* The zombie leader reserves this process-group ID until reaping.
             * On cancellation, finish any group members before releasing it. */
            if (stopping) kill(-pid, SIGKILL);
            pid_t waited;
            do { waited = waitpid(pid, &status, 0); } while (waited < 0 && errno == EINTR);
            if (waited == pid) break;
            wait_rc = -1;
        }
        if (wait_rc < 0 && errno != EINTR) {
            int saved = errno;
            if (pipefd[0] >= 0) close(pipefd[0]);
            sigprocmask(SIG_SETMASK, &previous, NULL);
            return errorf("cannot reap child: %s", strerror(saved));
        }
        fd_set ready;
        FD_ZERO(&ready);
        if (pipefd[0] >= 0) FD_SET(pipefd[0], &ready);
        struct timespec timeout = { .tv_sec = 1, .tv_nsec = 0 };
        if (pselect(pipefd[0] + 1, &ready, NULL, NULL, &timeout, &waiting) < 0 && errno != EINTR)
            io_error = true;
    }
    /* Never signal pid after waitpid has reaped it, even if a signal arrives
     * here. Nor do we wait for EOF from a stray inherited diagnostic writer. */
    if (pipefd[0] >= 0) {
        if (drain_diagnostics(pipefd[0], &diagnostic) < 0) io_error = true;
        close(pipefd[0]);
    }
    sigprocmask(SIG_SETMASK, &previous, NULL);
    if (cancelled) return errorf("cancelled by signal %d", (int)cancelled);
    if (io_error || !WIFEXITED(status) || WEXITSTATUS(status)) {
        if (diagnostic.truncated) fputs("resample: child diagnostic truncated (last 8192 bytes):\n", stderr);
        if (diagnostic.used) {
            fwrite(diagnostic.bytes, 1, diagnostic.used, stderr);
            if (diagnostic.bytes[diagnostic.used - 1] != '\n') fputc('\n', stderr);
        }
        if (io_error) return errorf("cannot read child diagnostics");
        if (WIFSIGNALED(status)) return errorf("%s terminated by signal %d", args[0], WTERMSIG(status));
        return errorf("%s failed (exit %d)", args[0], WEXITSTATUS(status));
    }
    return 0;
}

static bool same_file(const struct stat *a, const struct stat *b) {
    return a->st_dev == b->st_dev && a->st_ino == b->st_ino;
}

static int check_destination(int dirfd, const char *leaf, const Options *o) {
    struct stat dest;
    if (fstatat(dirfd, leaf, &dest, AT_SYMLINK_NOFOLLOW)) {
        if (errno == ENOENT) return 0;
        return errorf("cannot inspect output: %s", strerror(errno));
    }
    if (!o->overwrite) return errorf("output exists; use --overwrite");
    struct stat target;
    if (!fstatat(dirfd, leaf, &target, 0)) {
        for (size_t i = 0; i < o->count; ++i)
            if (same_file(&target, &o->inputs[i].st)) return errorf("output aliases input '%s'", o->inputs[i].name);
        if (!S_ISREG(target.st_mode)) return errorf("existing output must be a regular file");
    } else if (errno != ENOENT) return errorf("cannot inspect output target: %s", strerror(errno));
    return 0;
}

static int validate_inputs(Options *o) {
    for (size_t i = 0; i < o->count; ++i) {
        Input *in = &o->inputs[i];
        /* O_NONBLOCK prevents a path changed to a FIFO from hanging validation. */
        in->fd = open(in->name, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
        if (in->fd < 0) return errorf("cannot read input '%s': %s", in->name, strerror(errno));
        if (fstat(in->fd, &in->st)) return errorf("cannot inspect input '%s': %s", in->name, strerror(errno));
        if (!S_ISREG(in->st.st_mode) || in->st.st_size == 0) return errorf("input '%s' must be a nonempty local regular file", in->name);
    }
    return 0;
}

/* Reserve relative to the open directory, even if its pathname is renamed. */
static int reserve_temporary(int dirfd, char **name) {
    *name = allocate(64);
    int randomfd = open("/dev/urandom", O_RDONLY | O_CLOEXEC);
    if (randomfd < 0) return errorf("cannot open system random source: %s", strerror(errno));
    char candidate[64] = ".resample-";
    const char hex[] = "0123456789abcdef";
    for (int attempt = 0; attempt < 64; ++attempt) {
        unsigned char bytes[16];
        size_t used = 0;
        while (used < sizeof(bytes)) {
            ssize_t got = read(randomfd, bytes + used, sizeof(bytes) - used);
            if (got < 0 && errno == EINTR && !cancelled) continue;
            if (got <= 0 || cancelled) { close(randomfd); return errorf("cannot read system randomness or cancelled"); }
            used += (size_t)got;
        }
        for (size_t i = 0; i < sizeof(bytes); ++i) {
            candidate[10 + i*2] = hex[bytes[i] >> 4];
            candidate[11 + i*2] = hex[bytes[i] & 15];
        }
        candidate[42] = '\0';
        strcpy(*name, candidate);
        int fd = openat(dirfd, candidate, O_RDWR | O_CREAT | O_EXCL | O_CLOEXEC, 0600);
        if (fd >= 0) {
            close(randomfd);
            return fd;
        }
        if (errno != EEXIST) {
            int saved = errno;
            close(randomfd);
            return errorf("cannot reserve output temporary: %s", strerror(saved));
        }
    }
    close(randomfd);
    return errorf("cannot reserve a unique temporary after 64 attempts");
}

static char *graph(const Options *o) {
    /* Each segment is well below 256 bytes, including size_t and rate text. */
    if (o->count > (SIZE_MAX - 128) / 320) { errorf("too many inputs"); exit(1); }
    size_t cap = o->count * 320 + 128;
    char *result = allocate(cap);
    size_t used = 0;
    for (size_t i = 0; i < o->count; ++i)
        used += (size_t)snprintf(result + used, cap - used,
            "[%zu:a:0]aresample=48000,asetrate=%.8f,aresample=48000,"
            "aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=N/SR/TB[a%zu];", i, 48000 * o->ratio, i);
    for (size_t i = 0; i < o->count; ++i)
        used += (size_t)snprintf(result + used, cap - used, "[a%zu]", i);
    snprintf(result + used, cap - used, "concat=n=%zu:v=0:a=1[out]", o->count);
    return result;
}

static int render(Options *o) {
    assert(o->output && o->count); /* parse() establishes these preconditions. */
    int result = -1, dirfd = -1, tempfd = -1;
    bool temp_owned = false;
    char *parent = NULL, *temp = NULL, *filter = NULL;
    char **args = NULL, **fdnames = NULL;
    char *out = strdup(o->output);
    if (!out) return errorf("out of memory");
    char *slash = strrchr(out, '/');
    char *leaf = slash ? slash + 1 : out;
    if (slash) { *slash = '\0'; parent = realpath(*out ? out : "/", NULL); }
    else parent = realpath(".", NULL);
    if (!parent) { errorf("cannot resolve output parent: %s", strerror(errno)); goto cleanup; }
    dirfd = open(parent, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (dirfd < 0) { errorf("cannot open output directory: %s", strerror(errno)); goto cleanup; }
    if (validate_inputs(o) || check_destination(dirfd, leaf, o)) goto cleanup;

    /* Reserve descriptor targets beyond all input fds and transient pipe fds. */
    int base = 16;
    for (size_t i = 0; i < o->count; ++i) if (o->inputs[i].fd >= base) base = o->inputs[i].fd + 16;
    if (o->count > (size_t)(INT_MAX - base)) { errorf("too many inputs"); goto cleanup; }
    for (size_t i = 0; i < o->count; ++i) {
        char *fdpath = format("/dev/fd/%d", base);
        /* One encoded PCM frame proves decoded samples, unlike packet counts.
         * -frames:a bounds output without parsing ffprobe's buffered frame CSV
         * or terminating a healthy child. The complete render still uses -xerror. */
        char *probe[] = { "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-xerror", "-abort_on", "empty_output+empty_output_stream",
            "-protocol_whitelist", "file,pipe", "-i", fdpath, "-map", "0:a:0",
            "-frames:a", "1", "-c:a", "pcm_s16le", "-f", "null", "-", NULL };
        int rc = run_child(probe, o, -1, true, i, base);
        free(fdpath);
        if (rc) {
            errorf("input '%s' failed decoded-sample validation", o->inputs[i].name);
            goto cleanup;
        }
        if (lseek(o->inputs[i].fd, 0, SEEK_SET) < 0) { errorf("cannot rewind input: %s", strerror(errno)); goto cleanup; }
    }
    if (cancelled) { errorf("cancelled"); goto cleanup; }
    filter = graph(o);
    args = allocate((o->count * 4 + 40) * sizeof(*args));
    fdnames = allocate(o->count * sizeof(*fdnames));
    size_t n = 0;
#define ARG(s) args[n++] = (char *)(s)
    ARG("ffmpeg"); ARG("-hide_banner"); ARG("-loglevel"); ARG("error");
    ARG("-nostdin"); ARG("-y"); ARG("-xerror");
    /* Source validation and transformed-output validation are distinct: the
     * frozen graph can discard sub-millisecond inputs. Fail cleanly then. */
    ARG("-abort_on"); ARG("empty_output+empty_output_stream");
    for (size_t i = 0; i < o->count; ++i) {
        fdnames[i] = format("/dev/fd/%d", base + (int)i);
        ARG("-protocol_whitelist"); ARG("file,pipe"); ARG("-i"); ARG(fdnames[i]);
    }
    ARG("-filter_complex"); ARG(filter); ARG("-map"); ARG("[out]"); ARG("-map_metadata"); ARG("-1");
    ARG("-map_chapters"); ARG("-1");
    ARG("-c:a"); ARG(o->wav ? "pcm_s24le" : "libmp3lame");
    if (!o->wav) { ARG("-b:a"); ARG("320k"); }
    else { ARG("-rf64"); ARG("auto"); }
    ARG("-f"); ARG(o->wav ? "wav" : "mp3"); ARG("/dev/fd/1");
#undef ARG
    /* Finish all potentially allocating command construction before reservation. */
    tempfd = reserve_temporary(dirfd, &temp);
    if (tempfd < 0) goto cleanup;
    temp_owned = true;
    if (run_child(args, o, tempfd, false, 0, base)) goto cleanup;
    struct stat written, named;
    const char *tmp_leaf = temp;
    if (fstat(tempfd, &written) || fstatat(dirfd, tmp_leaf, &named, AT_SYMLINK_NOFOLLOW) ||
        !same_file(&written, &named) || written.st_size == 0) {
        errorf("temporary output changed or is empty; refusing publication"); goto cleanup;
    }
    if (close(tempfd)) { tempfd = -1; errorf("cannot close completed output: %s", strerror(errno)); goto cleanup; }
    tempfd = -1;
    sigset_t blocked, previous;
    cancel_set(&blocked);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous)) { errorf("cannot protect publication: %s", strerror(errno)); goto cleanup; }
    if (!cancelled && !check_destination(dirfd, leaf, o)) {
        /* rename replaces only the directory entry; link is atomic and exclusive. */
        int rc = o->overwrite ? renameat(dirfd, tmp_leaf, dirfd, leaf)
                              : linkat(dirfd, tmp_leaf, dirfd, leaf, 0);
        if (rc) errorf("cannot publish output: %s", strerror(errno));
        else result = 0;
    } else if (cancelled) errorf("cancelled before publication");
    sigprocmask(SIG_SETMASK, &previous, NULL);
cleanup:
    if (tempfd >= 0) close(tempfd);
    if (temp_owned && dirfd >= 0) {
        if (unlinkat(dirfd, temp, 0) && errno != ENOENT) errorf("cannot remove temporary '%s': %s", temp, strerror(errno));
    }
    if (dirfd >= 0) close(dirfd);
    if (fdnames) for (size_t i = 0; i < o->count; ++i) free(fdnames[i]);
    free(fdnames); free(args); free(filter); free(temp); free(parent); free(out);
    return result;
}

int main(int argc, char **argv) {
    /* Keep owned descriptors above stdio even when launched with closed streams. */
    for (int fd = 0; fd < 3; ++fd) {
        if (fcntl(fd, F_GETFD) < 0 && errno == EBADF) {
            if (open("/dev/null", O_RDWR) != fd) return 1;
        }
    }
    Options options = {0};
    int result = parse(argc, argv, &options);
    if (!result) {
        if (options.action == 1) fputs(usage, stdout);
        else if (options.action == 2) puts("resample-c 0.1.0 (resample-linked-v1)");
        else if (!(result = install_signals())) result = render(&options);
    }
    for (size_t i = 0; i < options.count; ++i) if (options.inputs[i].fd >= 0) close(options.inputs[i].fd);
    free(options.inputs);
    /* Publication is the commit point; cancellation after it is successful. */
    return result ? (cancelled ? 128 + (int)cancelled : 1) : 0;
}
