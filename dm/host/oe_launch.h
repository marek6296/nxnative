// Opaque same-program handoff across a fresh process. Stored beside DM_TEMP,
// outside the game's temporary mount and saves. iOS needs a manual relaunch.
#pragma once
#include <stddef.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
typedef struct {
    char magic[8]; uint64_t program; char build[32];
    uint32_t size; int32_t previous; unsigned char data[0x1000];
} oe_launch_record;
static oe_launch_record g_launch;
static int g_launch_available, g_launch_previous = -1;
static pthread_once_t g_launch_once = PTHREAD_ONCE_INIT;
static pthread_mutex_t g_launch_lock = PTHREAD_MUTEX_INITIALIZER;
static char *launch_path(void) {
    char *p = NULL; const char *t = getenv("DM_TEMP"), *root = getenv("DM_ROOT");
    if (t) asprintf(&p, "%s.launch", t);
    else if (root) asprintf(&p, "%s/run/platform/temp.launch", root);
    return p;
}
static int launch_io(int fd, void *buf, size_t n, int writing) {
    unsigned char *p = buf;
    while (n) {
        ssize_t r = writing ? write(fd, p, n) : read(fd, p, n);
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) { if (!r) errno = EIO; return 0; }
        p += r; n -= r;
    }
    return 1;
}
static void launch_load(void) {
    char *p = launch_path(); if (!p) return;
    int fd = open(p, O_RDONLY | O_NOFOLLOW);
    if (fd < 0) { if (errno != ENOENT) LOG("[RED] oe launch parameter open failed: %d", errno); free(p); return; }
    size_t header = offsetof(oe_launch_record, data); struct stat st;
    int ok = !fstat(fd, &st) && S_ISREG(st.st_mode) && st.st_size >= (off_t)header && st.st_size <= (off_t)sizeof g_launch;
    if (ok) ok = launch_io(fd, &g_launch, (size_t)st.st_size, 0);
    close(fd);
    ok = ok && !memcmp(g_launch.magic, "DMLAUN01", 8) && g_launch.program == DM_GAME_PROGRAM_ID &&
         !strncmp(g_launch.build, dm_main_build(), 32) && g_launch.size <= sizeof g_launch.data &&
         st.st_size == (off_t)(header + g_launch.size) && g_launch.previous >= 0 && g_launch.previous <= 255;
    if (ok && unlink(p)) { LOG("[RED] oe launch parameter could not be consumed: %d", errno); ok = 0; }
    if (ok) {
        g_launch_previous = g_launch.previous; g_launch_available = g_launch.size != 0;
        LOG("oe: restored %u launch parameter bytes from program %d", g_launch.size, g_launch_previous);
    } else LOG("[RED] oe: ignoring invalid or incompatible launch parameter file");
    free(p);
}
static int launch_store(const void *data, size_t n) {
    if (n > 0x1000 || (n && !data)) { errno = EINVAL; return 0; }
    char *p = launch_path(), *tmp = NULL;
    if (!p) { errno = ENOENT; return 0; }
    asprintf(&tmp, "%s.XXXXXX", p);
    oe_launch_record *r = calloc(1, sizeof *r);
    if (!tmp || !r) { free(p); free(tmp); free(r); errno = ENOMEM; return 0; }
    memcpy(r->magic, "DMLAUN01", 8); r->program = DM_GAME_PROGRAM_ID;
    strncpy(r->build, dm_main_build(), sizeof r->build);
    r->size = (uint32_t)n; r->previous = DM_GAME_PROGRAM_ID & 255;
    if (n) memcpy(r->data, data, n);
    int fd = mkstemp(tmp), ok = fd >= 0;
    if (ok) ok = launch_io(fd, r, offsetof(oe_launch_record, data) + n, 1) && !fsync(fd);
    int err = errno;
    if (fd >= 0 && close(fd) && ok) { ok = 0; err = errno; }
    if (ok && rename(tmp, p)) { ok = 0; err = errno; }
    if (!ok) unlink(tmp);
    free(r); free(tmp); free(p); errno = err;
    return ok;
}
