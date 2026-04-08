/*
 * usb_otg_reader.c  —  v4  (ioctl USBDEVFS_BULK, no libusb, FIFO output)
 *
 * Legge dati GNSS grezzi da ZED-F9P via USB OTG su Android/Termux.
 * NON usa libusb: usa ioctl(USBDEVFS_BULK) sull'fd passato da termux-usb.
 *
 * Output:
 *   - Se GNSS_OUT è impostata: scrive su quel path (FIFO o file) — BINARIO PURO
 *   - Altrimenti: stdout (solo per test diretti, non per uso con Python)
 *
 * Uso produzione (Python legge da FIFO):
 *   GNSS_OUT=/tmp/rilievopy_gnss.fifo termux-usb -e ./tools/usb_otg_reader <device>
 *
 * Uso diagnostica (scrivi su file):
 *   GNSS_OUT=~/gnss_raw.bin termux-usb -e ./tools/usb_otg_reader <device>
 *   poi: xxd ~/gnss_raw.bin | head -100
 *
 * Build: clang tools/usb_otg_reader.c -o tools/usb_otg_reader
 * (nessuna dipendenza esterna oltre libc)
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/usbdevice_fs.h>
#include <errno.h>

#define ENDPOINT_IN    0x82
#define READ_BUF_SIZE  4096
#define TIMEOUT_MS     2000

static volatile int g_running = 1;
static void handle_signal(int sig) { (void)sig; g_running = 0; }

int main(int argc, char **argv) {
    int fd;
    unsigned char buf[READ_BUF_SIZE];
    struct usbdevfs_bulktransfer bulk;
    FILE *out = stdout;

    if (argc < 2) {
        fprintf(stderr, "Uso: termux-usb -e ./tools/usb_otg_reader <device_path>\n");
        fprintf(stderr, "     GNSS_OUT=/tmp/rilievopy_gnss.fifo termux-usb -e ./tools/usb_otg_reader <device_path>\n");
        return 1;
    }
    if (sscanf(argv[1], "%d", &fd) != 1) {
        fprintf(stderr, "usb_otg_reader: fd non valido: '%s'\n", argv[1]);
        return 1;
    }

    /* Output: FIFO o file via env var GNSS_OUT (bypassa bash NUL-stripping) */
    const char *outpath = getenv("GNSS_OUT");
    if (outpath && outpath[0]) {
        out = fopen(outpath, "wb");
        if (!out) {
            fprintf(stderr, "usb_otg_reader: impossibile aprire output '%s': %s\n",
                    outpath, strerror(errno));
            return 1;
        }
        fprintf(stderr, "usb_otg_reader: output → %s\n", outpath);
    } else {
        fprintf(stderr, "usb_otg_reader: GNSS_OUT non impostata, output su stdout\n");
    }

    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    fprintf(stderr, "usb_otg_reader: fd=%d endpoint=0x%02x — avvio lettura\n",
            fd, ENDPOINT_IN);
    fflush(stderr);

    while (g_running) {
        memset(&bulk, 0, sizeof(bulk));
        bulk.ep      = ENDPOINT_IN;
        bulk.len     = READ_BUF_SIZE;
        bulk.data    = buf;
        bulk.timeout = TIMEOUT_MS;

        int r = ioctl(fd, USBDEVFS_BULK, &bulk);

        if (r > 0) {
            size_t written = fwrite(buf, 1, (size_t)r, out);
            fflush(out);
            if (written != (size_t)r) {
                fprintf(stderr, "usb_otg_reader: errore scrittura output (broken pipe?)\n");
                break;
            }
        } else if (r < 0) {
            if (errno == ETIMEDOUT || errno == EAGAIN) continue;
            if (errno == EINTR) break;
            fprintf(stderr, "usb_otg_reader: errore ioctl: %s (errno=%d)\n",
                    strerror(errno), errno);
            if (errno == ENODEV) {
                fprintf(stderr, "usb_otg_reader: device scollegato o permesso revocato\n");
                break;
            }
        }
    }

    if (out != stdout) fclose(out);
    fprintf(stderr, "usb_otg_reader: uscita pulita\n");
    return 0;
}
