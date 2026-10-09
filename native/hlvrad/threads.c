/* Work spread over the CPU's cores (vrad's RunThreadsOn): items handed out one at a time, in order.
 * Each item's result must go where only it writes, so the output doesn't depend on which thread did what. */
#include <windows.h>
#include <stdio.h>
#include "hlvrad.h"

int g_numthreads;                 /* (-threads N; 0: one per core) */

typedef struct {
    void (*fn)(int item, int thread);
    int count;
    volatile LONG next;
} work_t;

typedef struct { work_t *w; int thread; } worker_t;

static DWORD WINAPI Worker(LPVOID arg) {
    worker_t *k = arg;
    for (;;) {
        int i = (int)InterlockedIncrement(&k->w->next) - 1;
        if (i >= k->w->count) break;
        k->w->fn(i, k->thread);
    }
    return 0;
}

int NumThreads(void) {
    if (g_numthreads > 0) return g_numthreads > MAX_THREADS ? MAX_THREADS : g_numthreads;
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    int n = (int)si.dwNumberOfProcessors;
    return n < 1 ? 1 : n > MAX_THREADS ? MAX_THREADS : n;
}

/* (HLPROF=file: every millisecond, which code each worker is running; appended as raw addresses) */
static volatile LONG prof_stop;
static HANDLE prof_threads[MAX_THREADS];
static int prof_n;
static DWORD WINAPI Sampler(LPVOID arg) {
    FILE *f = fopen((const char *)arg, "ab");
    while (!prof_stop) {
        for (int t = 0; t < prof_n; t++) {
            CONTEXT c;
            c.ContextFlags = CONTEXT_CONTROL;
            if (SuspendThread(prof_threads[t]) == (DWORD)-1) continue;
            if (GetThreadContext(prof_threads[t], &c)) {
                /* (the module's name, then the offset in it) */
                HMODULE m = NULL;
                char name[64] = "?";
                if (GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                                       (LPCSTR)(size_t)c.Eip, &m) && m) {
                    char full[MAX_PATH];
                    GetModuleFileNameA(m, full, sizeof(full));
                    const char *b = strrchr(full, 92);           /* ('\\') */
                    snprintf(name, sizeof(name), "%s", b ? b + 1 : full);
                }
                unsigned int ip = (unsigned int)c.Eip - (unsigned int)(size_t)m;
                fwrite(name, 1, 64, f);
                fwrite(&ip, 4, 1, f);
            }
            ResumeThread(prof_threads[t]);
        }
        Sleep(1);
    }
    fclose(f);
    return 0;
}

void RunThreadsOn(int count, void (*fn)(int item, int thread)) {
    work_t w = {fn, count, 0};
    int n = NumThreads();
    if (n > count) n = count;
    if (n <= 1) {
        for (int i = 0; i < count; i++) fn(i, 0);
        return;
    }
    HANDLE h[MAX_THREADS];
    worker_t k[MAX_THREADS];
    for (int t = 0; t < n; t++) {
        k[t].w = &w, k[t].thread = t;
        h[t] = CreateThread(NULL, 8 << 20, Worker, &k[t], 0, NULL);      /* (8 MB stacks: deep tree walks) */
    }
    HANDLE sampler = NULL;
    if (getenv("HLPROF")) {
        prof_n = n, prof_stop = 0;
        for (int t = 0; t < n; t++) prof_threads[t] = h[t];
        sampler = CreateThread(NULL, 0, Sampler, getenv("HLPROF"), 0, NULL);
    }
    WaitForMultipleObjects(n, h, TRUE, INFINITE);
    if (sampler) {
        prof_stop = 1;
        WaitForSingleObject(sampler, INFINITE);
        CloseHandle(sampler);
    }
    for (int t = 0; t < n; t++) CloseHandle(h[t]);
}
