/* Passive brscan5 ABI probe. Logs numbers only; forwards every call unchanged.
 * Compile: cc -shared -fPIC -Wall -Wextra -Werror -o probe.so this_file.c -ldl
 * Use before the ATCN shim in LD_PRELOAD for a controlled scan session.
 * DS640_DIMENSIONS_LOG must point to a writable private log path.
 * Not installed or activated by the orientation experiment.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>

static atomic_uint events;

static void log_value(const char *name, const void *object, unsigned value) {
    int saved = errno;
    const char *path = getenv("DS640_DIMENSIONS_LOG");
    if (path && atomic_fetch_add(&events, 1) < 4096) {
        int fd = open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600);
        if (fd >= 0) {
            char line[192];
            int count = snprintf(line, sizeof(line), "pid=%ld %s object=%p value=%u\n",
                                 (long)getpid(), name, object, value);
            if (count > 0 && (size_t)count < sizeof(line)) {
                ssize_t written = write(fd, line, (size_t)count);
                (void)written;
            }
            close(fd);
        }
    }
    errno = saved;
}

static void *resolve(const char *symbol) {
    void *result = dlsym(RTLD_NEXT, symbol);
    if (!result) {
        /* A missing original is a probe setup error, never fabricate dimensions. */
        fprintf(stderr, "DS640 dimensions probe: missing %s\n", symbol);
        abort();
    }
    return result;
}

#define GETTER(symbol, type, label) \
    type symbol(const void *object) { \
        int saved = errno; \
        type (*original)(const void *) = resolve(#symbol); \
        errno = saved; \
        type value = original(object); \
        log_value(label, object, (unsigned)value); \
        return value; \
    }

GETTER(_ZNK15DeviceImageJpeg9GetHeightEv, unsigned, "received_jpeg_height")
GETTER(_ZNK16DeviceImageLines9GetHeightEv, unsigned, "received_line_count")
GETTER(_ZN11DeviceImage8GetWidthEv, unsigned, "received_width")
GETTER(_ZNK15DecodeParameter9GetHeightEv, unsigned, "requested_decode_height")
GETTER(_ZNK15DecodeParameter16WillAppendHeightEv, bool, "append_height")

void _ZN15DecodeParameter18SetDetectEndOfPageEb(void *object, bool enabled) {
    int saved = errno;
    void (*original)(void *, bool) = resolve("_ZN15DecodeParameter18SetDetectEndOfPageEb");
    errno = saved;
    original(object, enabled);
    log_value("detect_end_of_page", object, enabled);
}
