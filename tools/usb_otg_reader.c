/*
 * usb_otg_reader.c
 * ────────────────────────────────────────────────────────────────────────────
 * Reads raw GNSS data from a u-blox ZED-F9P connected via USB OTG on Android
 * (Termux). Designed to be invoked via:
 *
 *   termux-usb -e ./tools/usb_otg_reader <device_path>
 *
 * The Android runtime passes the USB file descriptor as argv[1] (integer).
 * We wrap it with libusb_wrap_sys_device(), claim the CDC Data interface (1),
 * and loop-read from bulk endpoint IN 0x82 writing raw bytes to stdout.
 * Python reads our stdout via subprocess.Popen and feeds the BytePipe.
 *
 * Hardcoded for ZED-F9P CDC layout:
 *   VID 0x1546 / PID 0x01a9
 *   Interface 1 (CDC Data, class 0x0a)
 *   Endpoint 0x82 (bulk IN, 64-byte max packet)
 *
 * Build (Termux):
 *   clang tools/usb_otg_reader.c -lusb-1.0 -o tools/usb_otg_reader
 *
 * Compile dependencies: pkg install libusb clang
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <signal.h>
#include <assert.h>
#include <libusb-1.0/libusb.h>

#define CDC_DATA_INTERFACE  1
#define ENDPOINT_IN         0x82
#define READ_TIMEOUT_MS     2000
#define READ_BUF_SIZE       4096

static volatile int g_running = 1;
static libusb_device_handle *g_handle = NULL;

static void handle_signal(int sig) {
    (void)sig;
    g_running = 0;
}

int main(int argc, char **argv) {
    libusb_context *ctx = NULL;
    libusb_device_handle *handle = NULL;
    int fd;
    int r;
    int transferred;
    unsigned char buf[READ_BUF_SIZE];

    if (argc < 2) {
        fprintf(stderr, "Usage: usb_otg_reader <fd>\n");
        fprintf(stderr, "  (invoked automatically by: termux-usb -e ./tools/usb_otg_reader <device>)\n");
        return 1;
    }

    if (sscanf(argv[1], "%d", &fd) != 1) {
        fprintf(stderr, "usb_otg_reader: invalid fd '%s'\n", argv[1]);
        return 1;
    }

    /* Signal handlers for clean exit */
    signal(SIGTERM, handle_signal);
    signal(SIGINT,  handle_signal);

    /* libusb: init first, then disable device discovery before doing anything else.
     * LIBUSB_OPTION_NO_DEVICE_DISCOVERY must be set before any enumeration attempt.
     * Passing the context (not NULL) is the preferred approach when context is available. */
    r = libusb_init(&ctx);
    if (r != 0) {
        fprintf(stderr, "usb_otg_reader: libusb_init failed: %s\n", libusb_error_name(r));
        return 1;
    }

    libusb_set_option(ctx, LIBUSB_OPTION_NO_DEVICE_DISCOVERY);

    r = libusb_wrap_sys_device(ctx, (intptr_t)fd, &handle);
    if (r != 0) {
        fprintf(stderr, "usb_otg_reader: libusb_wrap_sys_device failed: %s\n", libusb_error_name(r));
        libusb_exit(ctx);
        return 1;
    }
    g_handle = handle;

    /* Claim CDC Data interface */
    r = libusb_claim_interface(handle, CDC_DATA_INTERFACE);
    if (r != 0) {
        fprintf(stderr, "usb_otg_reader: claim interface %d failed: %s\n",
                CDC_DATA_INTERFACE, libusb_error_name(r));
        libusb_close(handle);
        libusb_exit(ctx);
        return 1;
    }

    fprintf(stderr, "usb_otg_reader: reading from endpoint 0x%02x interface %d\n",
            ENDPOINT_IN, CDC_DATA_INTERFACE);
    fflush(stderr);

    /* Main read loop: bulk-read and write to stdout */
    while (g_running) {
        transferred = 0;
        memset(buf, 0, sizeof(buf));

        r = libusb_bulk_transfer(handle, ENDPOINT_IN, buf, sizeof(buf),
                                 &transferred, READ_TIMEOUT_MS);

        if (r == 0 && transferred > 0) {
            /* Write raw bytes to stdout for Python to read */
            size_t written = fwrite(buf, 1, (size_t)transferred, stdout);
            if (written != (size_t)transferred) {
                /* stdout closed (Python parent exited) */
                break;
            }
            fflush(stdout);
        } else if (r == LIBUSB_ERROR_TIMEOUT) {
            /* Timeout is normal — device just has no data right now */
            continue;
        } else if (r == LIBUSB_ERROR_INTERRUPTED) {
            /* Signal received */
            break;
        } else if (r != 0) {
            fprintf(stderr, "usb_otg_reader: bulk_transfer error: %s\n",
                    libusb_error_name(r));
            break;
        }
    }

    /* Cleanup */
    libusb_release_interface(handle, CDC_DATA_INTERFACE);
    libusb_close(handle);
    libusb_exit(ctx);

    fprintf(stderr, "usb_otg_reader: exiting cleanly\n");
    return 0;
}
