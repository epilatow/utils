/*
 * BorgAdm.app wrapper -- compiled Mach-O binary for Full Disk Access.
 *
 * Why this exists, and why it re-spawns itself:
 *
 * macOS TCC does not ask "does the process reading files have Full
 * Disk Access?" It asks "does the *responsible process* have it?" For
 * a normal fork/exec child, the responsible process is inherited from
 * the parent and shared by the whole subtree -- it is NOT the program
 * that is actually running. FDA is also only ever granted to a code-
 * signed app bundle's Mach-O executable, never to an interpreted
 * script, which is why borgadm (a `uv run` Python script) cannot hold
 * the grant and this wrapper does.
 *
 * The grant is keyed to *this* binary's identity, and only applies
 * when this binary is the responsible process. When launchd launches
 * the wrapper directly, launchd makes it its own responsible process
 * and the grant applies. But when a scheduler such as crony spawns it
 * as a descendant (launchd -> crony -> /bin/sh -> wrapper), the
 * responsible process is the crony launchd job, which has no FDA, so
 * the check_fda() probe below would fail even though FDA is granted.
 *
 * To be robust to any launcher, the wrapper re-spawns itself once with
 * responsibility_spawnattrs_setdisclaim(), which resets the TCC
 * responsibility chain so the re-spawned process becomes its own
 * responsible process -- carrying this binary's (BorgAdm.app's)
 * identity and therefore its FDA grant. The descendants it then execs
 * and spawns (borgadm, and borg under it) inherit that responsibility,
 * so they read protected files under the grant -- the same
 * responsible-process relationship launchd sets up when it launches an
 * app directly. The re-spawn leaves the original instance waiting on
 * the new one, so it forwards termination signals down to it; a
 * scheduler timeout (a single-PID SIGTERM) or an interactive Ctrl-C
 * then still reaches borgadm, as it did when the wrapper exec'd into
 * borgadm directly, instead of being absorbed by this instance.
 *
 * responsibility_spawnattrs_setdisclaim() is undocumented SPI; Apple's
 * own LLDB, Chromium, and Qt Creator rely on it. It is resolved at run
 * time via dlsym and, if ever absent, the wrapper proceeds without it
 * rather than failing -- direct-launchd and Terminal launches already
 * make the wrapper its own responsible process, so the disclaim only
 * matters under a scheduler.
 *
 * Built automatically by: borgadm automate enable
 */

#include <dirent.h>
#include <dlfcn.h>
#include <errno.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>
#include <mach-o/dyld.h>

extern char **environ;

/* Exit code when FDA is not granted.  77 is the conventional
 * "skip / not configured" code (used by Automake, CMake/CTest,
 * Meson, etc.) and is outside the range used by borgadm itself,
 * making it easy to distinguish in launchd logs. */
#define FDA_EXIT_CODE 77

/* Env marker set on the re-spawned (disclaimed) instance so its pass
 * through main() skips the re-spawn instead of looping. */
#define DISCLAIM_ENV "BORGADM_FDA_DISCLAIMED"

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
 * process becomes its own responsible process -- carrying BorgAdm.app's
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
    /* SPI, not in any public header.  dlsym keeps the wrapper working
     * if a future macOS ever drops the symbol (we just run direct). */
    union {
        void *obj;
        int (*fn)(posix_spawnattr_t *, int);
    } setdisclaim;
    setdisclaim.obj =
        dlsym(RTLD_DEFAULT, "responsibility_spawnattrs_setdisclaim");
    if (!setdisclaim.obj)
        return;

    /* Read by the re-spawned process to break the loop; harmless if it
     * reaches borgadm/borg in the environment. */
    if (setenv(DISCLAIM_ENV, "1", 1) != 0)
        return;

    posix_spawnattr_t attr;
    if (posix_spawnattr_init(&attr) != 0)
        return;
    if (setdisclaim.fn(&attr, 1) != 0) {
        posix_spawnattr_destroy(&attr);
        return;
    }

    /* Block the signals we forward before spawning, so none is lost to
     * the default disposition -- which would kill this waiting instance
     * and orphan the child -- in the window before the handlers are in
     * place.  The child is given the prior mask so borgadm starts able
     * to receive them; their dispositions are still the default this
     * instance holds here, so the child inherits the default too. */
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

    /* Child is running borgadm now; never fall back to running it here
     * too.  Install the forwarders, then unblock: any signal that
     * arrived during setup is delivered now and forwarded to the child,
     * so this instance stays transparent to a crony timeout's SIGTERM
     * and to an interactive Ctrl-C. */
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
     * Resolve paths relative to this binary.
     * Binary:  <repo>/Applications/BorgAdm.app/Contents/MacOS/BorgAdm
     * App:     <repo>/Applications/BorgAdm.app  (3 levels up)
     * Repo:    <repo>                           (5 levels up)
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

    /* Derive the .app bundle path (3 levels up from binary). */
    char app_path[4096];
    snprintf(app_path, sizeof(app_path), "%s", real);
    dirname_n(app_path, 3);

    /*
     * Make BorgAdm.app the responsible process for the FDA check below
     * and everything it execs.  Skipped on the re-spawned instance
     * (marked via the environment) so this happens at most once.
     */
    if (!getenv(DISCLAIM_ENV))
        disclaim_respawn(real, argv);

    if (!check_fda()) {
        fprintf(stderr,
            "ERROR: BorgAdm.app does not have Full Disk Access.\n"
            "\n"
            "1. Run this command to open System Settings:\n"
            "\n"
            "   open \"x-apple.systempreferences:"
            "com.apple.settings.PrivacySecurity.extension"
            "?Privacy_AllFiles\"\n"
            "\n"
            "2. Click the '+' button\n"
            "3. Navigate to and add: %s\n"
            "4. Toggle the switch ON for BorgAdm\n",
            app_path);
        free(real);
        return FDA_EXIT_CODE;
    }

    dirname_n(real, 5);

    char borgadm[4096];
    snprintf(borgadm, sizeof(borgadm), "%s/bin/borgadm", real);
    free(real);

    /*
     * Security checks on borgadm before exec.
     *
     * This binary grants FDA to whatever it execs, so verify that
     * the target is executable, owned by us, and not writable by
     * group or others.  This prevents a same-system attacker from
     * substituting borgadm with a malicious script that would
     * inherit FDA.
     */
    struct stat st;
    if (stat(borgadm, &st) != 0) {
        fprintf(stderr, "ERROR: Cannot find borgadm at: %s\n", borgadm);
        return 1;
    }
    if (st.st_uid != getuid()) {
        fprintf(stderr,
            "ERROR: borgadm is not owned by the current user: %s\n",
            borgadm);
        return 1;
    }
    if (st.st_mode & (S_IWGRP | S_IWOTH)) {
        fprintf(stderr,
            "ERROR: borgadm is writable by group or others: %s\n",
            borgadm);
        return 1;
    }
    if (access(borgadm, X_OK) != 0) {
        fprintf(stderr, "ERROR: borgadm is not executable: %s\n", borgadm);
        return 1;
    }

    argv[0] = borgadm;
    execv(borgadm, argv);

    perror("execv");
    return 1;
}
