#include "internal.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

/* PrintWindow asks the window to draw itself into our DC, which is the only way
 * to read a DirectX game's own content: its back buffer never touches the
 * window's GDI surface, so BitBlt from the window DC comes back black.
 * PW_RENDERFULLCONTENT additionally makes DirectX and DirectComposition content
 * participate; without it the capture is black as well. Both flags live in the
 * SDK headers from Windows 8.1 on, so they are spelled out here. */
#define TH10_PW_CLIENTONLY 0x00000001u
#define TH10_PW_RENDERFULLCONTENT 0x00000002u

/* BMP headers are packed to the byte. Spelling them out keeps this file free of
 * <wingdi.h> ordering games, since BITMAPFILEHEADER uses a 2-byte type that the
 * compiler would otherwise pad. */
#pragma pack(push, 1)
typedef struct bmp_file_header {
    uint16_t type;
    uint32_t size;
    uint16_t reserved[2];
    uint32_t pixel_offset;
} bmp_file_header;

typedef struct bmp_info_header {
    uint32_t size;
    int32_t width;
    int32_t height;
    uint16_t planes;
    uint16_t bit_count;
    uint32_t compression;
    uint32_t image_size;
    int32_t x_pixels_per_meter;
    int32_t y_pixels_per_meter;
    uint32_t colors_used;
    uint32_t colors_important;
} bmp_info_header;
#pragma pack(pop)

static th10_capture_result capture_failure(th10_capture_result_tag tag, uint32_t win32_error) {
    return (th10_capture_result){.tag = tag, .win32_error = win32_error};
}

static th10_capture_result write_bitmap(const wchar_t *path, uint32_t width, uint32_t height,
                                        uint8_t *pixels) {
    const size_t pixel_size = (size_t)width * (size_t)height * 4u;
    bmp_file_header file_header;
    bmp_info_header info_header;
    size_t written = 0;
    size_t index;
    FILE *file;

    /* PrintWindow leaves the alpha channel at zero. A 32-bit BI_RGB bitmap is
     * documented to ignore it, but plenty of viewers render alpha zero as fully
     * transparent, so make every pixel opaque before writing. */
    for (index = 3; index < pixel_size; index += 4) {
        pixels[index] = 0xFFu;
    }

    memset(&file_header, 0, sizeof(file_header));
    file_header.type = 0x4D42u; /* 'BM' */
    file_header.pixel_offset = (uint32_t)(sizeof(file_header) + sizeof(info_header));
    file_header.size = (uint32_t)(file_header.pixel_offset + pixel_size);

    memset(&info_header, 0, sizeof(info_header));
    info_header.size = (uint32_t)sizeof(info_header);
    info_header.width = (int32_t)width;
    info_header.height = -(int32_t)height; /* negative: rows are stored top-down */
    info_header.planes = 1;
    info_header.bit_count = 32;
    info_header.compression = 0; /* BI_RGB */
    info_header.image_size = (uint32_t)pixel_size;

    file = _wfopen(path, L"wb");
    if (file == NULL) {
        return capture_failure(TH10_CAPTURE_FILE_OPEN_FAILED, (uint32_t)errno);
    }
    written += fwrite(&file_header, 1, sizeof(file_header), file);
    written += fwrite(&info_header, 1, sizeof(info_header), file);
    written += fwrite(pixels, 1, pixel_size, file);
    if (fclose(file) != 0 ||
        written != sizeof(file_header) + sizeof(info_header) + pixel_size) {
        return capture_failure(TH10_CAPTURE_FILE_WRITE_FAILED, (uint32_t)errno);
    }
    return (th10_capture_result){
        .tag = TH10_CAPTURE_SUCCESS,
        .width = width,
        .height = height,
    };
}

th10_capture_result th10_capture(th10_session *session, const wchar_t *path) {
    th10_capture_result result;
    RECT client;
    BITMAPINFO info;
    HDC screen_dc;
    HDC memory_dc;
    HBITMAP bitmap;
    HGDIOBJ previous;
    void *pixels = NULL;
    uint32_t width;
    uint32_t height;

    if (session == NULL || session->window == NULL || path == NULL) {
        return capture_failure(TH10_CAPTURE_INVALID_ARGUMENT, ERROR_SUCCESS);
    }
    if (!GetClientRect(session->window, &client)) {
        return capture_failure(TH10_CAPTURE_CLIENT_RECT_FAILED, GetLastError());
    }
    width = (uint32_t)(client.right - client.left);
    height = (uint32_t)(client.bottom - client.top);
    if (width == 0 || height == 0) {
        return capture_failure(TH10_CAPTURE_CLIENT_RECT_FAILED, ERROR_SUCCESS);
    }

    screen_dc = GetDC(NULL);
    if (screen_dc == NULL) {
        return capture_failure(TH10_CAPTURE_CREATE_DC_FAILED, GetLastError());
    }
    memory_dc = CreateCompatibleDC(screen_dc);
    if (memory_dc == NULL) {
        ReleaseDC(NULL, screen_dc);
        return capture_failure(TH10_CAPTURE_CREATE_DC_FAILED, GetLastError());
    }

    memset(&info, 0, sizeof(info));
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = (LONG)width;
    info.bmiHeader.biHeight = -(LONG)height; /* top-down, so rows match BMP order */
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;

    bitmap = CreateDIBSection(screen_dc, &info, DIB_RGB_COLORS, &pixels, NULL, 0);
    if (bitmap == NULL || pixels == NULL) {
        DeleteDC(memory_dc);
        ReleaseDC(NULL, screen_dc);
        return capture_failure(TH10_CAPTURE_CREATE_BITMAP_FAILED, GetLastError());
    }
    previous = SelectObject(memory_dc, bitmap);

    if (PrintWindow(session->window, memory_dc, TH10_PW_CLIENTONLY | TH10_PW_RENDERFULLCONTENT)) {
        result = write_bitmap(path, width, height, (uint8_t *)pixels);
    } else {
        result = capture_failure(TH10_CAPTURE_PRINT_WINDOW_FAILED, GetLastError());
    }

    SelectObject(memory_dc, previous);
    DeleteObject(bitmap);
    DeleteDC(memory_dc);
    ReleaseDC(NULL, screen_dc);
    return result;
}
