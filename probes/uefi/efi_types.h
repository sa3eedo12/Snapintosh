/* SPDX-License-Identifier: MIT */
/* Hand-written subset of the UEFI 2.x ABI used by the SP11 EL2 probe.
 *
 * Source: the public UEFI Specification (2.10), chapters 4 (system, boot and
 * runtime services), 7 (memory map), 9-10 (loaded image, device path), 12
 * (text output, graphics output) and 13 (file protocols). No EDK2 header was
 * copied; only the layouts the probe relies on are declared, and each one is
 * pinned by a _Static_assert so a typo cannot silently shift a field.
 *
 * Function slots the probe never calls are declared as `void *` placeholders.
 * They keep the offsets of the slots that are called honest, and they make an
 * accidental call a compile error rather than a jump through a typed pointer.
 *
 * AArch64 UEFI uses the AAPCS64 calling convention, which is what clang
 * produces for non-variadic functions on every target the probe is built for,
 * so no calling-convention annotation is needed (EFIAPI is empty).
 */
#ifndef PROBE_EFI_TYPES_H
#define PROBE_EFI_TYPES_H

#include <stddef.h>
#include <stdint.h>

#define EFIAPI

typedef uint64_t efi_status;
typedef uint64_t efi_uintn;
typedef void *efi_handle;
typedef uint16_t efi_char16;
typedef uint8_t efi_boolean;

typedef struct {
    uint32_t data1;
    uint16_t data2;
    uint16_t data3;
    uint8_t data4[8];
} efi_guid;

_Static_assert(sizeof(efi_guid) == 16, "EFI_GUID is 16 bytes");

#define EFI_ERROR_BIT 0x8000000000000000ull
#define EFI_ERR(n) (EFI_ERROR_BIT | (uint64_t)(n))
#define EFI_SUCCESS 0ull
#define EFI_LOAD_ERROR EFI_ERR(1)
#define EFI_INVALID_PARAMETER EFI_ERR(2)
#define EFI_UNSUPPORTED EFI_ERR(3)
#define EFI_BAD_BUFFER_SIZE EFI_ERR(4)
#define EFI_BUFFER_TOO_SMALL EFI_ERR(5)
#define EFI_NOT_READY EFI_ERR(6)
#define EFI_DEVICE_ERROR EFI_ERR(7)
#define EFI_WRITE_PROTECTED EFI_ERR(8)
#define EFI_OUT_OF_RESOURCES EFI_ERR(9)
#define EFI_VOLUME_FULL EFI_ERR(11)
#define EFI_NOT_FOUND EFI_ERR(14)
#define EFI_ACCESS_DENIED EFI_ERR(15)
#define EFI_ABORTED EFI_ERR(21)
#define EFI_IS_ERROR(s) (((s) & EFI_ERROR_BIT) != 0)

/* EFI_TABLE_HEADER, UEFI 2.10 section 4.2.1. */
typedef struct {
    uint64_t signature;
    uint32_t revision;
    uint32_t header_size;
    uint32_t crc32;
    uint32_t reserved;
} efi_table_header;

_Static_assert(sizeof(efi_table_header) == 24, "EFI_TABLE_HEADER is 24 bytes");

/* EFI_MEMORY_DESCRIPTOR, section 7.2. The firmware reports its own stride
 * (descriptor_size, usually 48 on AArch64); the 40 bytes below are the part
 * this probe understands. Always iterate by the reported stride. */
typedef struct {
    uint32_t type;
    uint32_t pad;
    uint64_t physical_start;
    uint64_t virtual_start;
    uint64_t number_of_pages;
    uint64_t attribute;
} efi_memory_descriptor;

_Static_assert(sizeof(efi_memory_descriptor) == 40, "EFI_MEMORY_DESCRIPTOR minimum is 40 bytes");

#define EFI_LOADER_DATA 2u
#define EFI_ALLOCATE_ANY_PAGES 0u

/* EFI_MEMORY_TYPE values the probe names or filters on (section 7.2). */
enum {
    EFI_MT_RESERVED = 0,
    EFI_MT_LOADER_CODE = 1,
    EFI_MT_LOADER_DATA = 2,
    EFI_MT_BOOT_CODE = 3,
    EFI_MT_BOOT_DATA = 4,
    EFI_MT_RUNTIME_CODE = 5,
    EFI_MT_RUNTIME_DATA = 6,
    EFI_MT_CONVENTIONAL = 7,
    EFI_MT_UNUSABLE = 8,
    EFI_MT_ACPI_RECLAIM = 9,
    EFI_MT_ACPI_NVS = 10,
    EFI_MT_MMIO = 11,
    EFI_MT_MMIO_PORT = 12,
    EFI_MT_PAL_CODE = 13,
    EFI_MT_PERSISTENT = 14,
};

/* Simple Text Output Protocol, section 12.4. Only OutputString is called. */
typedef struct efi_simple_text_output {
    void *reset;
    efi_status (*output_string)(struct efi_simple_text_output *self, const efi_char16 *text);
    void *test_string;
    void *query_mode;
    void *set_mode;
    void *set_attribute;
    void *clear_screen;
    void *set_cursor_position;
    void *enable_cursor;
    void *mode;
} efi_simple_text_output;

_Static_assert(offsetof(efi_simple_text_output, output_string) == 8, "OutputString slot");

/* EFI_RUNTIME_SERVICES, section 4.5. */
typedef struct {
    efi_table_header hdr;
    void *get_time;
    void *set_time;
    void *get_wakeup_time;
    void *set_wakeup_time;
    void *set_virtual_address_map;
    void *convert_pointer;
    void *get_variable;
    void *get_next_variable_name;
    void *set_variable;
    void *get_next_high_monotonic_count;
    void (*reset_system)(uint32_t reset_type, efi_status status, efi_uintn data_size, void *data);
    void *update_capsule;
    void *query_capsule_capabilities;
    void *query_variable_info;
} efi_runtime_services;

_Static_assert(offsetof(efi_runtime_services, reset_system) == 104, "ResetSystem slot");
_Static_assert(sizeof(efi_runtime_services) == 136, "EFI_RUNTIME_SERVICES size");

#define EFI_RESET_COLD 0u

/* EFI_BOOT_SERVICES, section 4.4. */
typedef struct {
    efi_table_header hdr;
    void *raise_tpl;
    void *restore_tpl;
    void *allocate_pages;
    void *free_pages;
    efi_status (*get_memory_map)(efi_uintn *map_size, efi_memory_descriptor *map, efi_uintn *map_key,
                                 efi_uintn *descriptor_size, uint32_t *descriptor_version);
    efi_status (*allocate_pool)(uint32_t pool_type, efi_uintn size, void **buffer);
    efi_status (*free_pool)(void *buffer);
    void *create_event;
    void *set_timer;
    void *wait_for_event;
    void *signal_event;
    void *close_event;
    void *check_event;
    void *install_protocol_interface;
    void *reinstall_protocol_interface;
    void *uninstall_protocol_interface;
    efi_status (*handle_protocol)(efi_handle handle, const efi_guid *protocol, void **interface);
    void *reserved;
    void *register_protocol_notify;
    efi_status (*locate_handle)(uint32_t search_type, const efi_guid *protocol, void *search_key,
                                efi_uintn *buffer_size, efi_handle *buffer);
    void *locate_device_path;
    void *install_configuration_table;
    void *load_image;
    void *start_image;
    void *exit;
    void *unload_image;
    efi_status (*exit_boot_services)(efi_handle image, efi_uintn map_key);
    void *get_next_monotonic_count;
    efi_status (*stall)(efi_uintn microseconds);
    efi_status (*set_watchdog_timer)(efi_uintn timeout, uint64_t watchdog_code, efi_uintn data_size,
                                     efi_char16 *watchdog_data);
    void *connect_controller;
    void *disconnect_controller;
    void *open_protocol;
    void *close_protocol;
    void *open_protocol_information;
    void *protocols_per_handle;
    void *locate_handle_buffer;
    efi_status (*locate_protocol)(const efi_guid *protocol, void *registration, void **interface);
} efi_boot_services;

_Static_assert(offsetof(efi_boot_services, get_memory_map) == 56, "GetMemoryMap slot");
_Static_assert(offsetof(efi_boot_services, allocate_pool) == 64, "AllocatePool slot");
_Static_assert(offsetof(efi_boot_services, free_pool) == 72, "FreePool slot");
_Static_assert(offsetof(efi_boot_services, handle_protocol) == 152, "HandleProtocol slot");
_Static_assert(offsetof(efi_boot_services, locate_handle) == 176, "LocateHandle slot");
_Static_assert(offsetof(efi_boot_services, exit_boot_services) == 232, "ExitBootServices slot");
_Static_assert(offsetof(efi_boot_services, stall) == 248, "Stall slot");
_Static_assert(offsetof(efi_boot_services, set_watchdog_timer) == 256, "SetWatchdogTimer slot");
_Static_assert(offsetof(efi_boot_services, open_protocol) == 280, "OpenProtocol slot");
_Static_assert(offsetof(efi_boot_services, locate_handle_buffer) == 312, "LocateHandleBuffer slot");
_Static_assert(offsetof(efi_boot_services, locate_protocol) == 320, "LocateProtocol slot");

#define EFI_LOCATE_BY_PROTOCOL 2u

/* EFI_CONFIGURATION_TABLE, section 4.6. */
typedef struct {
    efi_guid vendor_guid;
    void *vendor_table;
} efi_configuration_table;

_Static_assert(sizeof(efi_configuration_table) == 24, "EFI_CONFIGURATION_TABLE size");

/* EFI_SYSTEM_TABLE, section 4.3. */
typedef struct {
    efi_table_header hdr;
    efi_char16 *firmware_vendor;
    uint32_t firmware_revision;
    uint32_t pad;
    efi_handle console_in_handle;
    void *con_in;
    efi_handle console_out_handle;
    efi_simple_text_output *con_out;
    efi_handle standard_error_handle;
    efi_simple_text_output *std_err;
    efi_runtime_services *runtime_services;
    efi_boot_services *boot_services;
    efi_uintn number_of_table_entries;
    efi_configuration_table *configuration_table;
} efi_system_table;

_Static_assert(offsetof(efi_system_table, firmware_vendor) == 24, "FirmwareVendor slot");
_Static_assert(offsetof(efi_system_table, firmware_revision) == 32, "FirmwareRevision slot");
_Static_assert(offsetof(efi_system_table, console_out_handle) == 56, "ConsoleOutHandle slot");
_Static_assert(offsetof(efi_system_table, con_out) == 64, "ConOut slot");
_Static_assert(offsetof(efi_system_table, runtime_services) == 88, "RuntimeServices slot");
_Static_assert(offsetof(efi_system_table, boot_services) == 96, "BootServices slot");
_Static_assert(offsetof(efi_system_table, number_of_table_entries) == 104, "NumberOfTableEntries slot");
_Static_assert(offsetof(efi_system_table, configuration_table) == 112, "ConfigurationTable slot");
_Static_assert(sizeof(efi_system_table) == 120, "EFI_SYSTEM_TABLE size");

/* EFI_LOADED_IMAGE_PROTOCOL, section 9.1. */
typedef struct {
    uint32_t revision;
    uint32_t pad;
    efi_handle parent_handle;
    efi_system_table *system_table;
    efi_handle device_handle;
    void *file_path;
    void *reserved;
    uint32_t load_options_size;
    uint32_t pad2;
    void *load_options;
    void *image_base;
    uint64_t image_size;
    uint32_t image_code_type;
    uint32_t image_data_type;
    void *unload;
} efi_loaded_image;

_Static_assert(offsetof(efi_loaded_image, device_handle) == 24, "DeviceHandle slot");
_Static_assert(offsetof(efi_loaded_image, image_base) == 64, "ImageBase slot");

/* EFI_FILE_PROTOCOL, section 13.5. The revision-2 entries are not used. */
typedef struct efi_file {
    uint64_t revision;
    efi_status (*open)(struct efi_file *self, struct efi_file **new_handle, const efi_char16 *name,
                       uint64_t open_mode, uint64_t attributes);
    efi_status (*close)(struct efi_file *self);
    void *delete_file;
    void *read;
    efi_status (*write)(struct efi_file *self, efi_uintn *size, const void *buffer);
    void *get_position;
    void *set_position;
    void *get_info;
    void *set_info;
    efi_status (*flush)(struct efi_file *self);
} efi_file;

_Static_assert(offsetof(efi_file, open) == 8, "Open slot");
_Static_assert(offsetof(efi_file, close) == 16, "Close slot");
_Static_assert(offsetof(efi_file, write) == 40, "Write slot");
_Static_assert(offsetof(efi_file, flush) == 80, "Flush slot");

#define EFI_FILE_MODE_READ 0x0000000000000001ull
#define EFI_FILE_MODE_WRITE 0x0000000000000002ull
#define EFI_FILE_MODE_CREATE 0x8000000000000000ull
#define EFI_FILE_DIRECTORY 0x0000000000000010ull

/* EFI_SIMPLE_FILE_SYSTEM_PROTOCOL, section 13.4. */
typedef struct efi_simple_fs {
    uint64_t revision;
    efi_status (*open_volume)(struct efi_simple_fs *self, efi_file **root);
} efi_simple_fs;

/* EFI_GRAPHICS_OUTPUT_PROTOCOL, section 12.9. SetMode and Blt are never
 * called: the probe stays passive and draws into the linear framebuffer. */
typedef struct {
    uint32_t red_mask;
    uint32_t green_mask;
    uint32_t blue_mask;
    uint32_t reserved_mask;
} efi_pixel_bitmask;

typedef struct {
    uint32_t version;
    uint32_t horizontal_resolution;
    uint32_t vertical_resolution;
    uint32_t pixel_format;
    efi_pixel_bitmask pixel_information;
    uint32_t pixels_per_scan_line;
} efi_gop_mode_info;

_Static_assert(sizeof(efi_gop_mode_info) == 36, "EFI_GRAPHICS_OUTPUT_MODE_INFORMATION size");

typedef struct {
    uint32_t max_mode;
    uint32_t mode;
    efi_gop_mode_info *info;
    efi_uintn size_of_info;
    uint64_t frame_buffer_base;
    efi_uintn frame_buffer_size;
} efi_gop_mode;

_Static_assert(offsetof(efi_gop_mode, info) == 8, "GOP Mode.Info slot");
_Static_assert(offsetof(efi_gop_mode, frame_buffer_base) == 24, "GOP FrameBufferBase slot");
_Static_assert(sizeof(efi_gop_mode) == 40, "EFI_GRAPHICS_OUTPUT_PROTOCOL_MODE size");

typedef struct {
    void *query_mode;
    void *set_mode;
    void *blt;
    efi_gop_mode *mode;
} efi_gop;

_Static_assert(offsetof(efi_gop, mode) == 24, "GOP Mode slot");

/* EFI_GRAPHICS_PIXEL_FORMAT, section 12.9.2. */
enum {
    EFI_GOP_RGBX = 0,
    EFI_GOP_BGRX = 1,
    EFI_GOP_BITMASK = 2,
    EFI_GOP_BLT_ONLY = 3,
};

/* Protocol and configuration-table GUIDs (UEFI 2.10 and ACPI 6.5 appendix). */
#define EFI_GUID_LOADED_IMAGE \
    { 0x5B1B31A1, 0x9562, 0x11D2, { 0x8E, 0x3F, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B } }
#define EFI_GUID_DEVICE_PATH \
    { 0x09576E91, 0x6D3F, 0x11D2, { 0x8E, 0x39, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B } }
#define EFI_GUID_SIMPLE_FS \
    { 0x964E5B22, 0x6459, 0x11D2, { 0x8E, 0x39, 0x00, 0xA0, 0xC9, 0x69, 0x72, 0x3B } }
#define EFI_GUID_GOP \
    { 0x9042A9DE, 0x23DC, 0x4A38, { 0x96, 0xFB, 0x7A, 0xDE, 0xD0, 0x80, 0x51, 0x6A } }
#define EFI_GUID_ACPI_20 \
    { 0x8868E871, 0xE4F1, 0x11D3, { 0xBC, 0x22, 0x00, 0x80, 0xC7, 0x3C, 0x88, 0x81 } }
#define EFI_GUID_ACPI_10 \
    { 0xEB9D2D30, 0x2D88, 0x11D3, { 0x9A, 0x16, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0x4D } }
#define EFI_GUID_SMBIOS \
    { 0xEB9D2D31, 0x2D88, 0x11D3, { 0x9A, 0x16, 0x00, 0x90, 0x27, 0x3F, 0xC1, 0x4D } }
#define EFI_GUID_SMBIOS3 \
    { 0xF2FD1544, 0x9794, 0x4A2C, { 0x99, 0x2E, 0xE5, 0xBB, 0xCF, 0x20, 0xE3, 0x94 } }

#endif /* PROBE_EFI_TYPES_H */
