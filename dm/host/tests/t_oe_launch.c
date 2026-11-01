// A program restart must deliver its opaque parameters to a fresh process once.
#include "../hle.h"
#ifdef TEST_SECOND_PROGRAM
#undef DM_GAME_PROGRAM_ID
#define DM_GAME_PROGRAM_ID 0x010015100B514001ULL
#endif
#include "../hle_oe.c"
#define CURRENT_INDEX ((int)(DM_GAME_PROGRAM_ID & 255))
#include <sys/wait.h>
#include <unistd.h>
#include <signal.h>
module_t *g_mod; int g_nmod;
void dm_thread_native_wait(int entering) { (void)entering; }
uint64_t now_ns(void) { return 0; }
void dm_audio_suspend(int on) { (void)on; }
void guest_clock_freeze_for(int reason, int on) { (void)reason; (void)on; }
void dm_gfx_suspend(int on) { (void)on; }
void hle_bind_system_event(void *g, int ac) { (void)g; (void)ac; }
void hle_signal_system_event(void *g) { (void)g; }
void hle_clear_system_event(void *g) { (void)g; }
static const unsigned char payload[] = {0x41, 0, 0x32, 0xff, 0x15};
static void expected_abort(int sig) { (void)sig; _exit(99); }
static int run(int action) {
    fflush(stdout);
    pid_t p = fork(); if (p < 0) return -1;
    if (!p) {
        alarm(5);
        if (action == 0) { h_oe_ExecProgram(CURRENT_INDEX, payload, sizeof payload); _exit(91); }
        if (action == 4) { h_oe_ExecProgram(CURRENT_INDEX, NULL, 0); _exit(91); }
        if (action == 6) { signal(SIGABRT, expected_abort); h_oe_ExecProgram(CURRENT_INDEX, payload, 0x1001); _exit(91); }
        if (action == 9) { signal(SIGABRT, expected_abort); h_oe_ExecProgram(CURRENT_INDEX ^ 1, payload, sizeof payload); _exit(91); }
        if (action == 8) { static module_t mod = {.name = "main", .build_id = "0123456789abcdef0123456789abcdef"}; g_mod = &mod; g_nmod = 1; }
        size_t n = 999; unsigned char b[8] = {0};
        if (action == 1) {
            if (h_oe_PrevProgram() != CURRENT_INDEX || !h_oe_PopLaunchParam(&n, b, sizeof b) || n != 5 || memcmp(b, payload, 5)) _exit(92);
            if (h_oe_PopLaunchParam(&n, b, sizeof b)) _exit(93);
        } else if (action == 2) {
            if (h_oe_PrevProgram() != -1 || h_oe_PopLaunchParam(&n, b, sizeof b)) _exit(94);
        } else if (action == 3) {
            // SDK 11 copies min(buffer size, storage size), but returns the full size.
            if (!h_oe_PopLaunchParam(&n, b, 2) || n != 5 || b[0] != 0x41 || b[1] != 0 || b[2] != 0) _exit(95);
            if (h_oe_PopLaunchParam(&n, b, sizeof b)) _exit(96);
        } else if (action == 5) {
            if (h_oe_PrevProgram() != CURRENT_INDEX || h_oe_PopLaunchParam(&n, b, sizeof b)) _exit(97);
        } else if (action == 7 || action == 8) {
            if (h_oe_PrevProgram() != -1 || h_oe_PopLaunchParam(&n, b, sizeof b)) _exit(98);
        }
        _exit(0);
    }
    int st; if (waitpid(p, &st, 0) != p) return -1;
    return WIFEXITED(st) ? WEXITSTATUS(st) : -1;
}
int main(void) {
    char dir[] = "/tmp/dm-launch-test.XXXXXX";
    if (!mkdtemp(dir)) return 1;
    static module_t mod = {.name = "main", .build_id = "fedcba9876543210fedcba9876543210"}; g_mod = &mod; g_nmod = 1;
    char temp[512]; snprintf(temp, sizeof temp, "%s/temp", dir); setenv("DM_TEMP", temp, 1);
    int a = run(0), b = run(1), c = run(2), d = run(0), e = run(3);
    printf("restart=%d delivery=%d consumed=%d restart2=%d short-buffer=%d\n", a, b, c, d, e);
    int bad = a || b || c || d || e;
    a = run(9); bad |= a != 99;
    printf("other-program rejected=%d\n", a);
    a = run(4); b = run(5); c = run(6); bad |= a || b || c != 99;
    printf("empty-restart=%d empty-delivery=%d oversize-rejected=%d\n", a, b, c);
    char file[600]; snprintf(file, sizeof file, "%s.launch", temp);
    a = run(0); truncate(file, 10); b = run(7); bad |= a || b;
    a = run(0); b = run(8); bad |= a || b;
    printf("truncated or different-build parameter ignored: %s\n", bad ? "FAIL" : "OK");
    unlink(file);
    rmdir(dir);
    return !!bad;
}
