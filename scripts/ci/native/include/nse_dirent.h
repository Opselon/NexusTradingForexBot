/* nse_dirent.h — portable directory iteration.
 *
 * POSIX platforms use the real <dirent.h>. MSVC ships none, so on _WIN32
 * the same opendir/readdir/closedir surface is implemented on the Win32
 * file-enumeration API (FindFirstFileA / FindNextFileA). Only d_name is
 * provided — the engine never reads d_type. "." and ".." are returned like
 * POSIX does; callers skip them. Paths may use '/' separators: Win32
 * accepts them, and the engine builds every path with '/'.
 */
#ifndef NSE_DIRENT_H
#define NSE_DIRENT_H

#if defined(_WIN32)
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

struct dirent {
    char d_name[MAX_PATH];
};

typedef struct {
    HANDLE handle;
    WIN32_FIND_DATAA data;
    int first;
    struct dirent ent;
} DIR;

static DIR *opendir(const char *path) {
    DIR *d = (DIR *)calloc(1, sizeof(DIR));
    char pattern[MAX_PATH];
    if (!d) return NULL;
    if (snprintf(pattern, sizeof(pattern), "%s\\*", path) >= (int)sizeof(pattern)) {
        free(d);
        return NULL;
    }
    d->handle = FindFirstFileA(pattern, &d->data);
    if (d->handle == INVALID_HANDLE_VALUE) {
        free(d);
        return NULL;
    }
    d->first = 1;
    return d;
}

static struct dirent *readdir(DIR *d) {
    if (!d) return NULL;
    if (d->first) {
        d->first = 0;
    } else if (!FindNextFileA(d->handle, &d->data)) {
        return NULL;
    }
    /* cFileName is bounded by MAX_PATH, same as d_name. */
    snprintf(d->ent.d_name, sizeof(d->ent.d_name), "%s", d->data.cFileName);
    return &d->ent;
}

static int closedir(DIR *d) {
    if (!d) return -1;
    if (d->handle != INVALID_HANDLE_VALUE) FindClose(d->handle);
    free(d);
    return 0;
}

#else
#include <dirent.h>
#endif

#endif /* NSE_DIRENT_H */
