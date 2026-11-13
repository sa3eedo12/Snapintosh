/* SPDX-License-Identifier: MIT */
/* Stand-in EFI image for tools/test-sp11-autorun-qemu.py: it prints one fixed line and returns (or, built with
 * DUMMY_HANG, never returns). No reviewed binary (slbounce, q1n1, the X2 tests) is needed to rehearse the autorun
 * script, so what runs under the emulator is this file built once per name the script mentions.
 *
 * The same source is built twice over: as an EFI runtime driver for the slbounce names, because the Shell's `load`
 * refuses an image that is not a driver, and as an EFI application for the others, because the Shell runs those
 * as commands. DUMMY_TAG names the stand-in; the printed line also carries the image's load options, which is how
 * the test sees that the Shell passed the arguments the script wrote.
 *
 * Only the UEFI ABI slots used below are declared, with the offsets the UEFI Specification 2.10 gives them (system
 * table 4.3, boot services 7.1, loaded image 9.1, text output 12.4); the static assertions pin them.
 */
#include <stddef.h>
#include <stdint.h>

#ifndef DUMMY_TAG
#error "DUMMY_TAG names the stand-in, for example -DDUMMY_TAG=\"Q1N1.EFI\""
#endif
#ifndef DUMMY_KIND
#error "DUMMY_KIND is DRIVER or APP"
#endif
#ifndef DUMMY_STATUS
#define DUMMY_STATUS 0ull /* EFI_SUCCESS; EFI_LOAD_ERROR (0x8000000000000001) rehearses a driver that fails to start */
#endif

typedef uint64_t efi_status;
typedef void *efi_handle;
typedef struct {
    uint32_t data1;
    uint16_t data2;
    uint16_t data3;
    uint8_t data4[8];
} efi_guid;

typedef struct text_output {
    void *reset;
    efi_status (*output_string)(struct text_output *self, const uint16_t *text);
} text_output;

typedef struct {
    uint8_t header[24];
    void *raise_tpl, *restore_tpl, *allocate_pages, *free_pages, *get_memory_map, *allocate_pool, *free_pool;
    void *create_event, *set_timer, *wait_for_event, *signal_event, *close_event, *check_event;
    void *install_protocol_interface, *reinstall_protocol_interface, *uninstall_protocol_interface;
    efi_status (*handle_protocol)(efi_handle handle, const efi_guid *protocol, void **interface);
    void *reserved, *register_protocol_notify, *locate_handle, *locate_device_path, *install_configuration_table;
    void *load_image, *start_image, *exit, *unload_image, *exit_boot_services, *get_next_monotonic_count;
    efi_status (*stall)(uint64_t microseconds);
} boot_services;

typedef struct {
    uint8_t header[24];
    void *firmware_vendor;
    uint32_t firmware_revision;
    void *console_in_handle, *con_in, *console_out_handle;
    text_output *con_out;
    void *standard_error_handle, *std_err, *runtime_services;
    boot_services *boot;
} system_table;

typedef struct {
    uint32_t revision;
    efi_handle parent_handle;
    system_table *system;
    efi_handle device_handle;
    void *file_path;
    void *reserved;
    uint32_t load_options_size;
    void *load_options;
} loaded_image;

_Static_assert(offsetof(system_table, con_out) == 64, "ConOut slot");
_Static_assert(offsetof(system_table, boot) == 96, "BootServices slot");
_Static_assert(offsetof(text_output, output_string) == 8, "OutputString slot");
_Static_assert(offsetof(boot_services, handle_protocol) == 152, "HandleProtocol slot");
_Static_assert(offsetof(boot_services, stall) == 248, "Stall slot");
_Static_assert(offsetof(loaded_image, load_options_size) == 48, "LoadOptionsSize slot");
_Static_assert(offsetof(loaded_image, load_options) == 56, "LoadOptions slot");

static const efi_guid loaded_image_guid = {0x5B1B31A1, 0x9562, 0x11D2,
                                           {0x8E, 0x3F, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B}};

static uint16_t line[256];
static size_t used;

static void put(const char *text)
{
    while (*text && used < 250) {
        line[used++] = (uint8_t)*text++;
    }
}

static void put_wide(const uint16_t *text, uint32_t bytes)
{
    for (uint32_t i = 0; i < bytes / 2 && text[i] && used < 250; i++) {
        line[used++] = text[i];
    }
}

efi_status efi_main(efi_handle image, system_table *st)
{
    loaded_image *li = 0;

    put("AUTORUN-DUMMY " DUMMY_KIND " " DUMMY_TAG " OPTIONS=[");
    if (st->boot->handle_protocol(image, &loaded_image_guid, (void **)&li) == 0 && li && li->load_options) {
        put_wide(li->load_options, li->load_options_size);
    }
    put("]\r\n");
    line[used] = 0;
    st->con_out->output_string(st->con_out, line);
#ifdef DUMMY_HANG
    for (;;) {
        st->boot->stall(1000000);
    }
#endif
    return DUMMY_STATUS;
}
