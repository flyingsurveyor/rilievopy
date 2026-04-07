/*
 * usb_otg_reader.c
 * ────────────────────────────────────────────────────────────────────────────
 * Reads raw GNSS data from a u-blox ZED-F9P connected via USB OTG on Android
 * (Termux). Designed to be invoked via:
 *
 *   termux-usb -e ./tools/usb_otg_reader <device_path>
 *
 * The Android runtime passes the USB file descriptor as argv[1] (integer).
 * We use ioctl(fd, USBDEVFS_BULK, ...) directly — no libusb required.
 * Reads from bulk endpoint IN 0x82, writes raw bytes to stdout.
 * Python reads our stdout via subprocess.Popen and feeds the BytePipe.
 *
 * Hardcoded for ZED-F9P CDC layout:
 *   Interface 1 (CDC Data, class 0x0a)
 *   Endpoint 0x82 (bulk IN)
 *
 * Build (Termux):
 *   clang tools/usb_otg_reader.c -o tools/usb_otg_reader
 *
 * Compile dependencies: pkg install clang
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <errno.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/usbdevice_fs.h>

#define ENDPOINT_IN      0x82
#define READ_TIMEOUT_MS  2000
#define READ_BUF_SIZE    4096

static volatile int g_running = 1;

static void handle_signal(int sig) {
    (void)sig;
    g_running = 0;
}

int main(int argc, char **argv) {
    int fd;
    unsigned char buf[READ_BUF_SIZE];

    if (argc < 2) {
        fprintf(stderr, "Usage: usb_otg_reader <fd>\n");
        fprintf(stderr, "  (invoked automatically by: termux-usb -e ./tools/usb_otg_reader <device>)\n");
        return 1;
    }

    if (sscanf(argv[1], "%d", &fd) != 1 || fd < 0) {
        fprintf(stderr, "usb_otg_reader: invalid fd '%s'\n", argv[1]);
        return 1;
    }

    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    fprintf(stderr, "usb_otg_reader: fd=%d endpoint=0x%02x\n", fd, ENDPOINT_IN);
    fflush(stderr);

    /* Main read loop: ioctl USBDEVFS_BULK on endpoint IN */
    while (g_running) {
        struct usbdevfs_bulktransfer bulk;
        memset(&bulk, 0, sizeof(bulk));
        bulk.ep      = ENDPOINT_IN;
        bulk.len     = READ_BUF_SIZE;
        bulk.timeout = READ_TIMEOUT_MS;
        bulk.data    = buf;

        int r = ioctl(fd, USBDEVFS_BULK, &bulk);

        if (r > 0) {
            /* r is the number of bytes transferred */
            size_t written = fwrite(buf, 1, (size_t)r, stdout);
            if (written != (size_t)r) {
                /* stdout closed — Python parent exited */
                break;
            }
            fflush(stdout);
        } else if (r == 0) {
            /* Zero bytes — treat as timeout/no data, continue */
            continue;
        } else {
            /* r == -1: check errno */
            int err = errno;
            if (err == ETIMEDOUT || err == EAGAIN) {
                /* Normal — no data ready on endpoint */
                continue;
            } else if (err == ENODEV || err == ENOENT) {
                fprintf(stderr, "usb_otg_reader: device disconnected (%s, errno=%d)\n",
                        strerror(err), err);
                break;
            } else if (err == EINTR) {
                /* Interrupted by signal */
                break;
            } else {
                fprintf(stderr, "usb_otg_reader: ioctl USBDEVFS_BULK error: %s (errno=%d)\n",
                        strerror(err), err);
                break;
            }
        }
    }

    fprintf(stderr, "usb_otg_reader: exiting cleanly\n");
    return 0;
}
