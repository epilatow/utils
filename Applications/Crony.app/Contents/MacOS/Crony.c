/*
 * Crony.app wrapper -- compiled Mach-O binary for Full Disk Access.
 *
 * Why this exists, and why it re-spawns itself:
 *
 * macOS TCC does not ask "does the process reading files have Full
 * Disk Access?" It asks "does the *responsible process* have it?" For
 * a normal fork/exec child, the responsible process is inherited from
 * the parent and shared by the whole subtree -- it is NOT the program
 * that is actually running. FDA is also only ever granted to a code-
 * signed app bundle's Mach-O executable, never to an interpreted
 * script, so crony (a `uv run` Python script) cannot hold the grant.
 * This wrapper does, and crony's runner execs a job's command through
 * it so the command reads protected files under the grant.
 *
 * The grant is keyed to *this* binary's identity, and only applies
 * when this binary is the responsible process. When launchd launches
 * the wrapper directly, launchd makes it its own responsible process
 * and the grant applies. But crony runs it as a descendant of its own
 * launchd job (launchd -> crony -> wrapper), so the responsible
 * process is the crony job, which has no FDA, and the check_fda()
 * probe below would fail even though FDA is granted.
 *
 * To be robust to any launcher, the wrapper re-spawns itself once with
 * responsibility_spawnattrs_setdisclaim(), which resets the TCC
 * responsibility chain so the re-spawned process becomes its own
 * responsible process -- carrying this binary's (Crony.app's) identity
 * and therefore its FDA grant. The command it then execs (and that
 * command's own children) inherit that responsibility, so they read
 * protected files under the grant -- the same responsible-process
 * relationship launchd sets up when it launches an app directly. The
 * re-spawn leaves the original instance waiting on the new one, so it
 * forwards termination signals down to it; a scheduler timeout (a
 * single-PID SIGTERM) or an interactive Ctrl-C then still reaches the
 * command, instead of being absorbed by this instance.
 *
 * responsibility_spawnattrs_setdisclaim() is undocumented SPI; Apple's
 * own LLDB, Chromium, and Qt Creator rely on it. It is resolved at run
 * time via dlsym and, if ever absent, the wrapper proceeds without it
 * rather than failing -- direct-launchd and Terminal launches already
 * make the wrapper its own responsible process, so the disclaim only
 * matters under a scheduler.
 *
 * Two modes, both running after the disclaim so they reflect this
 * binary's grant:
 *   --check-fda      Probe FDA and exit 0 (granted) or FDA_EXIT_CODE
 *                    (denied), without running anything. Used by
 *                    `crony apply` / `crony status` to test the grant.
 *   <cmd> [args...]  Require FDA, then exec <cmd> with [args...] so it
 *                    runs with Full Disk Access. <cmd> is whatever
 *                    crony's runner would have run directly (it builds
 *                    the argv); the wrapper imposes no restriction on
 *                    it -- crony is itself a `uv run` script whose
 *                    interpreter a local user could replace, so a
 *                    target allowlist here would add no real security.
 *
 * Built automatically by `crony apply` when a job needs FDA.
 */

#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

/* Exit code when FDA is not granted.  77 is the conventional
 * "skip / not configured" code (used by Automake, CMake/CTest,
 * Meson, etc.) and is outside crony's own run-outcome range,
 * making it easy to distinguish in launchd logs and probe results. */
#define FDA_EXIT_CODE 77

/* First argument that switches the wrapper into probe mode (test FDA,
 * run nothing); mirrors _CHECK_FDA_FLAG in crony.platform.fda. */
#define CHECK_FDA_FLAG "--check-fda"

/* Env marker set on the re-spawned (disclaimed) instance so its pass
 * through main() skips the re-spawn instead of looping. */
#define DISCLAIM_ENV "CRONY_FDA_DISCLAIMED"

/*
 * Check Full Disk Access by probing a TCC-protected directory.
 * Returns 1 if accessible (FDA granted) or indeterminate,
 * 0 if denied (EACCES/EPERM).
 */
static int check_fda(void)
{
    const char *home = getenv("HOME");
    if (!home)
        return 1;

    char path[1024];
    snprintf(path, sizeof(path),
             "%s/Library/Application Support/com.apple.TCC", home);

    DIR *d = opendir(path);
    if (d) {
        closedir(d);
        return 1;
    }
    if (errno == EACCES || errno == EPERM)
        return 0;
    /* ENOENT or other -- can't determine, proceed. */
    return 1;
}

/* Print, to stderr, how to grant Full Disk Access to this bundle. */
static void print_fda_guidance(const char *app_path)
{
    fprintf(stderr,
            "ERROR: Crony.app does not have Full Disk Access.\n"
            "\n"
            "1. Run this command to open System Settings:\n"
            "\n"
            "   open \"x-apple.systempreferences:"
            "com.apple.settings.PrivacySecurity.extension"
            "?Privacy_AllFiles\"\n"
            "\n"
            "2. Click the '+' button\n"
            "3. Navigate to and add: %s\n"
            "4. Toggle the switch ON for Crony\n",
            app_path);
}

/* PID of the re-spawned child, for the signal-forwarding handler. */
static volatile sig_atomic_t g_child_pid = 0;

/* Forward a termination signal to the re-spawned child so this waiting
 * instance is transparent to a scheduler timeout or an interactive
 * Ctrl-C.  async-signal-safe: only touches a sig_atomic_t and kill(). */
static void forward_signal(int sig)
{
    if (g_child_pid > 0)
        kill(g_child_pid, sig);
}

/*
 * Re-spawn this binary with TCC responsibility disclaimed so the new
 * process becomes its own responsible process -- carrying Crony.app's
 * identity, which holds the FDA grant -- regardless of how this wrapper
 * was launched.  self is this binary's resolved path; argv is forwarded
 * unchanged.
 *
 * Returns only when nothing was spawned (the SPI is unavailable, or the
 * spawn failed) so the caller can proceed without disclaiming.  Once the
 * child exists this does not return: it waits, forwarding termination
 * signals to it, and _exit()s with the child's status.
 */
static void disclaim_respawn(const char *self, char *const argv[])
{
    /*
     * SPI, not in any public header.  dlsym keeps the wrapper working
     * if a future macOS ever drops the symbol (we just run direct).
     */
    union {
        void *obj;
        int (*fn)(posix_spawnattr_t *, int);
    } setdisclaim;
    setdisclaim.obj =
        dlsym(RTLD_DEFAULT, "responsibility_spawnattrs_setdisclaim");
    if (!setdisclaim.obj)
        return;

    /*
     * Read by the re-spawned process to break the loop; harmless if it
     * reaches the command in the environment.
     */
    if (setenv(DISCLAIM_ENV, "1", 1) != 0)
        return;

    posix_spawnattr_t attr;
    if (posix_spawnattr_init(&attr) != 0)
        return;
    if (setdisclaim.fn(&attr, 1) != 0) {
        posix_spawnattr_destroy(&attr);
        return;
    }

    /*
     * Block the signals we forward before spawning, so none is lost to
     * the default disposition -- which would kill this waiting instance
     * and orphan the child -- in the window before the handlers are in
     * place.  The child is given the prior mask so the command starts
     * able to receive them; their dispositions are still the default
     * this instance holds here, so the child inherits the default too.
     */
    sigset_t forwarded, prev_mask;
    sigemptyset(&forwarded);
    sigaddset(&forwarded, SIGTERM);
    sigaddset(&forwarded, SIGINT);
    sigaddset(&forwarded, SIGHUP);
    sigaddset(&forwarded, SIGQUIT);
    sigprocmask(SIG_BLOCK, &forwarded, &prev_mask);
    posix_spawnattr_setsigmask(&attr, &prev_mask);
    posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSIGMASK);

    pid_t pid;
    int rc = posix_spawn(&pid, self, NULL, &attr, argv, environ);
    posix_spawnattr_destroy(&attr);
    if (rc != 0) {
        sigprocmask(SIG_SETMASK, &prev_mask, NULL);
        return;
    }

    /*
     * Child is running the command now; never fall back to running it
     * here too.  Install the forwarders, then unblock: any signal that
     * arrived during setup is delivered now and forwarded to the child,
     * so this instance stays transparent to a crony timeout's SIGTERM
     * and to an interactive Ctrl-C.
     */
    g_child_pid = pid;
    struct sigaction sa;
    memset(&sa, 0, sizeof(sa));
    sa.sa_handler = forward_signal;
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    sigaction(SIGHUP, &sa, NULL);
    sigaction(SIGQUIT, &sa, NULL);
    sigprocmask(SIG_SETMASK, &prev_mask, NULL);

    int status;
    while (waitpid(pid, &status, 0) < 0) {
        if (errno != EINTR)
            _exit(1);
    }
    if (WIFEXITED(status))
        _exit(WEXITSTATUS(status));
    if (WIFSIGNALED(status))
        _exit(128 + WTERMSIG(status));
    _exit(1);
}

/*
 * Walk up count directory levels from path, in place.
 * E.g. dirname_n("/a/b/c/d", 2) -> "/a/b"
 */
static void dirname_n(char *path, int count)
{
    for (int i = 0; i < count; i++) {
        char *slash = strrchr(path, '/');
        if (slash && slash != path)
            *slash = '\0';
        else
            break;
    }
}

int main(int argc __attribute__((unused)), char *argv[])
{
    /*
     * Resolve this binary's own path for the disclaim re-spawn, and the
     * .app bundle path (3 levels up) for the FDA-denied guidance:
     *   <repo>/Applications/Crony.app/Contents/MacOS/Crony
     *   <repo>/Applications/Crony.app  (3 levels up)
     */
    char self_buf[4096];
    uint32_t bufsize = sizeof(self_buf);
    if (_NSGetExecutablePath(self_buf, &bufsize) != 0) {
        fprintf(stderr, "ERROR: Cannot determine executable path\n");
        return 1;
    }
    char *real = realpath(self_buf, NULL);
    if (!real) {
        fprintf(stderr, "ERROR: Cannot resolve executable path\n");
        return 1;
    }
    char app_path[4096];
    snprintf(app_path, sizeof(app_path), "%s", real);
    dirname_n(app_path, 3);

    /*
     * Make Crony.app the responsible process for the FDA check below
     * and the command it execs.  Skipped on the re-spawned instance
     * (marked via the environment) so this happens at most once.
     */
    if (!getenv(DISCLAIM_ENV))
        disclaim_respawn(real, argv);
    free(real);

    int probe = argv[1] && strcmp(argv[1], CHECK_FDA_FLAG) == 0;

    if (!check_fda()) {
        /*
         * The probe reports denial through its exit code alone; a real
         * run logs the guidance so a failed launchd job explains itself.
         */
        if (!probe)
            print_fda_guidance(app_path);
        return FDA_EXIT_CODE;
    }
    if (probe)
        return 0;

    if (!argv[1]) {
        fprintf(stderr, "ERROR: no command given to Crony.app\n");
        return 1;
    }

    execv(argv[1], &argv[1]);
    perror("execv");
    return 1;
}
