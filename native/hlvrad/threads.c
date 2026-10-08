/* Work spread over the CPU's cores (vrad's RunThreadsOn): items handed out one at a time, in order.
 * Each item's result must go where only it writes, so the output doesn't depend on which thread did what. */
#include <windows.h>
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
    WaitForMultipleObjects(n, h, TRUE, INFINITE);
    for (int t = 0; t < n; t++) CloseHandle(h[t]);
}
