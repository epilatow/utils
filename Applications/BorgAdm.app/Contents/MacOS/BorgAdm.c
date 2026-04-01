/*
 * BorgAdm.app wrapper — compiled Mach-O binary for FDA propagation.
 *
 * macOS TCC only grants Full Disk Access to app bundles whose
 * executable is a native Mach-O binary (not an interpreted script).
 * This wrapper checks that FDA is active, resolves the borgadm
 * script path relative to its own location, and execs it with
 * all original arguments.
 *
 * Built automatically by: borgadm automate enable
 */

#include <dirent.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include <mach-o/dyld.h>

/* Exit code when FDA is not granted.  77 is the conventional
 * "skip / not configured" code (used by Automake, CMake/CTest,
 * Meson, etc.) and is outside the range used by borgadm itself,
 * making it easy to distinguish in launchd logs. */
#define FDA_EXIT_CODE 77

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
    /* ENOENT or other — can't determine, proceed. */
    return 1;
}

/*
 * Walk up count directory levels from path, in place.
 * E.g. dirname_n("/a/b/c/d", 2) → "/a/b"
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
