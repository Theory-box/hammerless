/* -live: the session's link to its owner (see LiveSession in direct.c). Commands come as lines on stdin, read by a
 * thread so the lighting work never waits on a pipe; the lightmaps go out through a named piece of shared memory the
 * owner (Blender) maps too. */
#include <windows.h>
#include "hlvrad.h"

typedef struct liveline_s { struct liveline_s *next; char text[1]; } liveline_t;
static liveline_t *first, *last;
static CRITICAL_SECTION lock;
static HANDLE have;                 /* (set while lines wait, or stdin has ended) */
static int ended;

static DWORD WINAPI ReadThread(LPVOID arg) {
    (void)arg;
    char buf[4096];
    while (fgets(buf, sizeof(buf), stdin)) {
        size_t n = strlen(buf);
        while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r' || buf[n - 1] == ' ' || buf[n - 1] == '\t')) buf[--n] = 0;
        liveline_t *l = xalloc(sizeof(liveline_t) + n);
        memcpy(l->text, buf, n + 1);
        EnterCriticalSection(&lock);
        if (last) last->next = l;
        else first = l;
        last = l;
        SetEvent(have);
        LeaveCriticalSection(&lock);
    }
    EnterCriticalSection(&lock);
    ended = 1;
    SetEvent(have);
    LeaveCriticalSection(&lock);
    return 0;
}

void LiveStartInput(void) {
    InitializeCriticalSection(&lock);
    have = CreateEventA(NULL, TRUE, FALSE, NULL);
    CreateThread(NULL, 0, ReadThread, NULL, 0, NULL);
}

/* the next command line: 1 with the line, 0 when none waits (wait 0), -1 when stdin has ended and all were read */
int LiveGetLine(char *buf, int size, int wait) {
    for (;;) {
        EnterCriticalSection(&lock);
        liveline_t *l = first;
        if (l) {
            first = l->next;
            if (!first) last = NULL;
        }
        int done = !l && ended;
        if (!first && !ended) ResetEvent(have);
        LeaveCriticalSection(&lock);
        if (l) {
            snprintf(buf, (size_t)size, "%s", l->text);
            free(l);
            return 1;
        }
        if (done) return -1;
        if (!wait) return 0;
        WaitForSingleObject(have, INFINITE);
    }
}

/* a rest of up to ms milliseconds, ended at once by a command (PACE: the card left to the owner's viewport) */
void LiveRest(int ms) {
    if (ms > 0) WaitForSingleObject(have, (DWORD)ms);
}

/* whether a command waits (or stdin has ended): long work looks between its steps */
int LivePending(void) {
    EnterCriticalSection(&lock);
    int p = first != NULL || ended;
    LeaveCriticalSection(&lock);
    return p;
}

/* the shared memory, by name (NULL: it couldn't be made) */
unsigned char *LiveMap(const char *name, size_t size) {
    HANDLE h = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, (DWORD)((unsigned long long)size >> 32),
                                  (DWORD)(size & 0xffffffffu), name);
    if (!h) return NULL;
    return MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, size);
}

/* (the generation counter's writes seen before and after the data's) */
void LiveBarrier(void) { MemoryBarrier(); }
