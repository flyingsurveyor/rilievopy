/*
 * usb_otg_reader.c  —  v3  (ioctl USBDEVFS_BULK, no libusb)
 * Reads raw GNSS data from ZED-F9P via USB OTG on Android/Termux.
 * Invoked via: termux-usb -e ./tools/usb_otg_reader <device_path>
 * termux-usb passes the Android USB fd as argv[1].
 * Writes raw bytes to stdout, diagnostics to stderr.
 * Build: clang tools/usb_otg_reader.c -o tools/usb_otg_reader
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <time.h>
#include <sys/ioctl.h>
#include <linux/usbdevice_fs.h>
#include <errno.h>

#define ENDPOINT_IN       0x82
#define READ_BUF_SIZE     4096
#define TIMEOUT_MS        5000
#define HEARTBEAT_SECS    5

static volatile int g_running = 1;
static void handle_signal(int sig) { (void)sig; g_running = 0; }

static void ts(void) {
    time_t t = time(NULL);
    struct tm *tm = localtime(&t);
    char b[16];
    strftime(b, sizeof(b), "%H:%M:%S", tm);
    fprintf(stderr, "[%s] ", b);
}

static void dump_hex(const unsigned char *d, int len, int max) {
    int n = len < max ? len : max;
    fprintf(stderr, "  hex:");
    for (int i = 0; i < n; i++) fprintf(stderr, " %02x", d[i]);
    if (len > max) fprintf(stderr, " ...");
    fprintf(stderr, "\n  asc:");
    for (int i = 0; i < n; i++) {
        unsigned char c = d[i];
        fprintf(stderr, "  %c", (c >= 0x20 && c < 0x7f) ? c : '.');
    }
    if (len > max) fprintf(stderr, " ...");
    fprintf(stderr, "\n");
}

int main(int argc, char **argv) {
    int fd;
    if (argc < 2) {
        fprintf(stderr, "Uso: termux-usb -e ./tools/usb_otg_reader <device_path>\n");
        return 1;
    }
    if (sscanf(argv[1], "%d", &fd) != 1) {
        fprintf(stderr, "usb_otg_reader: fd non valido: '%s'\n", argv[1]);
        return 1;
    }
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    fprintf(stderr, "usb_otg_reader v3: fd=%d endpoint=0x%02x timeout=%dms\n",
            fd, ENDPOINT_IN, TIMEOUT_MS);
    fprintf(stderr, "  Lettura ZED-F9P avviata. SIGTERM/SIGINT per fermare.\n");
    fflush(stderr);

    unsigned char buf[READ_BUF_SIZE];
    struct usbdevfs_bulktransfer bulk;
    long long total_bytes = 0, total_ok = 0, total_timeout = 0, total_err = 0;
    time_t last_hb = time(NULL);
    int first = 1;

    while (g_running) {
        memset(&bulk, 0, sizeof(bulk));
        bulk.ep      = ENDPOINT_IN;
        bulk.len     = READ_BUF_SIZE;
        bulk.data    = buf;
        bulk.timeout = TIMEOUT_MS;

        int r = ioctl(fd, USBDEVFS_BULK, &bulk);

        if (r > 0) {
            total_bytes += r;
            total_ok++;
            if (first) {
                fprintf(stderr, "\n*** PRIMO DATO RICEVUTO! %d byte ***\n", r);
                dump_hex(buf, r, 24);
                first = 0;
            }
            size_t written = fwrite(buf, 1, (size_t)r, stdout);
            fflush(stdout);
            if (written != (size_t)r) {
                fprintf(stderr, "usb_otg_reader: stdout chiuso, esco.\n");
                break;
            }
        } else if (r == 0) {
            continue;
        } else {
            if (errno == ETIMEDOUT || errno == EAGAIN) {
                total_timeout++;
            } else if (errno == EINTR) {
                fprintf(stderr, "\nusb_otg_reader: interrotto da segnale.\n");
                break;
            } else if (errno == ENODEV || errno == ENOENT) {
                ts();
                fprintf(stderr, "ERRORE: device scollegato (errno=%d). Esco.\n", errno);
                break;
            } else {
                total_err++;
                ts();
                fprintf(stderr, "ERRORE ioctl: %s (errno=%d)\n", strerror(errno), errno);
                if (errno == EPIPE || errno == EIO) {
                    fprintf(stderr, "  → errore fatale, esco.\n");
                    break;
                }
                /* errori transienti: continua */
            }
        }

        time_t now = time(NULL);
        if (now - last_hb >= HEARTBEAT_SECS) {
            ts();
            fprintf(stderr, "stats: %lld byte, %lld ok, %lld timeout, %lld errori\n",
                    total_bytes, total_ok, total_timeout, total_err);
            last_hb = now;
        }
    }

    fprintf(stderr, "\nusb_otg_reader: fine. totale=%lld byte ok=%lld timeout=%lld err=%lld\n",
            total_bytes, total_ok, total_timeout, total_err);
    return 0;
}
