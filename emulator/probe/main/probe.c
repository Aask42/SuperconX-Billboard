/* Emulator self-test firmware: PSRAM, LittleFS, UART input, and the emulator contract
 * (badge_emu_fb_* planes, EMU_EPD_REFRESH, @btn, @fb) from emulator/README.md. */
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "driver/uart.h"
#include "esp_littlefs.h"

#define W 960
#define H 672
#define STRIDE (W / 8)
#define PLANE (STRIDE * H)

uint8_t *badge_emu_fb_black;
uint8_t *badge_emu_fb_red;

static int s_refresh;
static int s_dot_x = W / 2;
static int s_red = 1;

static void set_px(uint8_t *plane, int x, int y)
{
    plane[y * STRIDE + x / 8] |= 0x80 >> (x % 8);
}

static void render(void)
{
    memset(badge_emu_fb_black, 0, PLANE);
    memset(badge_emu_fb_red, 0, PLANE);
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            int dx = x - s_dot_x, dy = y - H / 2;
            if (x < 20 || y < 20 || x >= W - 20 || y >= H - 20) set_px(badge_emu_fb_black, x, y);
            else if (dx * dx + dy * dy < 120 * 120) set_px(s_red ? badge_emu_fb_red : badge_emu_fb_black, x, y);
        }
    }
    printf("EMU_EPD_REFRESH %d\n", ++s_refresh);
}

static void b64_out(const uint8_t *p, size_t n)
{
    static const char t[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    char q[5] = {0};
    for (size_t i = 0; i < n; i += 3) {
        uint32_t v = p[i] << 16 | (i + 1 < n ? p[i + 1] << 8 : 0) | (i + 2 < n ? p[i + 2] : 0);
        q[0] = t[v >> 18 & 63];
        q[1] = t[v >> 12 & 63];
        q[2] = i + 1 < n ? t[v >> 6 & 63] : '=';
        q[3] = i + 2 < n ? t[v & 63] : '=';
        fputs(q, stdout);
    }
}

static void dump_fb(void)
{
    printf("EMU_FB %d %d raw ", W, H);
    b64_out(badge_emu_fb_black, PLANE);
    b64_out(badge_emu_fb_red, PLANE);
    printf("\n");
}

static void handle_line(const char *line)
{
    if (strncmp(line, "@btn ", 5) == 0) {
        const char *b = line + 5;
        if (strcmp(b, "menu_minus") == 0 && s_dot_x > 160) s_dot_x -= 80;
        else if (strcmp(b, "menu_plus") == 0 && s_dot_x < W - 160) s_dot_x += 80;
        else if (strcmp(b, "a") == 0) s_red = 1;
        else if (strcmp(b, "b") == 0) s_red = 0;
        printf("BTN:%s\n", b);
        render();
    } else if (strcmp(line, "@fb") == 0) {
        dump_fb();
    } else {
        printf("ECHO:%s\n", line);
    }
}

void app_main(void)
{
    ESP_LOGI("probe", "psram free=%u", (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    badge_emu_fb_black = heap_caps_calloc(1, PLANE, MALLOC_CAP_SPIRAM);
    badge_emu_fb_red = heap_caps_calloc(1, PLANE, MALLOC_CAP_SPIRAM);
    if (!badge_emu_fb_black || !badge_emu_fb_red) {
        ESP_LOGE("probe", "PLANES_FAIL");
        return;
    }
    ESP_LOGI("probe", "PLANES_READY");
    esp_vfs_littlefs_conf_t fs = {.base_path = "/data", .partition_label = "storage", .dont_mount = false};
    if (esp_vfs_littlefs_register(&fs) == ESP_OK) {
        FILE *f = fopen("/data/sub/hello.txt", "r");
        char txt[64] = {0};
        if (f) {
            fgets(txt, sizeof(txt), f);
            fclose(f);
        }
        printf("FS:%s", f ? txt : "missing\n");
    } else {
        printf("FS:mount failed\n");
    }
    render();
    uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    char line[128];
    size_t n = 0;
    for (;;) {
        uint8_t c;
        if (uart_read_bytes(UART_NUM_0, &c, 1, portMAX_DELAY) != 1) continue;
        if (c == '\r' || c == '\n') {
            line[n] = 0;
            if (n) handle_line(line);
            n = 0;
        } else if (n < sizeof(line) - 1) {
            line[n++] = (char)c;
        }
    }
}
