/*
 * app_main.cpp — SuperconX Billboard native firmware (no LVGL, direct framebuffer).
 *
 * Draws straight into the e-paper driver's two 1-bpp planes (epd.h) and drives a
 * button-based app switcher (badge_core state machine + LED menu bar). Implements
 * the emulator contract (T55) so the QEMU harness / behave specs can drive it:
 *   - epd exposes badge_emu_fb_black/red and logs EMU_EPD_REFRESH <n> per refresh
 *   - badge_leds logs EMU_LED lines
 *   - EMU_READY is printed once the console is reading input
 *   - a UART line "@btn <name>" injects a button; "@app <name>" launches an app
 *
 * App *content* (real PNG/text/schedule/LLM rendering) is deliberately minimal
 * here — each app draws an identifiable placeholder; issues #6/#7/#8 fill it in.
 */
#include <cstdio>
#include <cstring>
#include <cstdint>

#include "esp_err.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "nvs_flash.h"
#include "esp_littlefs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern "C" {
#include "epd.h"
#include "badge_core.h"
}
#include "badge_leds.hpp"

#if CONFIG_BADGE_EMULATOR
#include "driver/uart.h"
#endif

static const char *TAG = "badge";

namespace {

struct App { const char *name; };
/* Index 0 launches by default; order mirrors the stock app set. */
const App kApps[] = {
    {"nametag"}, {"gallery"}, {"schedule"}, {"workshops"}, {"terminal"}, {"tinystories"},
};
constexpr int kAppCount = (int)(sizeof(kApps) / sizeof(kApps[0]));

badge_switcher_t g_sw;

void fill_rect_black(int x0, int y0, int w, int h)
{
    uint8_t *blk = epd_black_plane();
    uint8_t *red = epd_red_plane();
    for (int y = y0; y < y0 + h && y < EPD_HEIGHT; ++y) {
        for (int x = x0; x < x0 + w && x < EPD_WIDTH; ++x) {
            badge_plane_put(blk, red, x, y, EPD_STRIDE_BYTES, BADGE_PX_BLACK);
        }
    }
}

/* Placeholder render: a border plus a per-app block so the screen is non-blank
 * and distinguishable. Replaced by real app rendering in issues #6/#7/#8. */
void render_app(int idx)
{
    epd_clear();
    fill_rect_black(0, 0, EPD_WIDTH, 8);
    fill_rect_black(0, EPD_HEIGHT - 8, EPD_WIDTH, 8);
    fill_rect_black(0, 0, 8, EPD_HEIGHT);
    fill_rect_black(EPD_WIDTH - 8, 0, 8, EPD_HEIGHT);
    fill_rect_black(40 + idx * 120, 60, 100, 100);
    epd_refresh();
    ESP_LOGI(TAG, "rendered app '%s'", kApps[idx].name);
}

void show_leds()
{
    if (g_sw.selecting) {
        badge::leds::show_menu((size_t)g_sw.selection, (size_t)kAppCount);
    } else {
        badge::leds::fill(0, 0, 0);
    }
}

void launch(int idx)
{
    g_sw.active = idx;
    g_sw.selecting = 0;
    ESP_LOGI(TAG, "launch %s", kApps[idx].name);
    badge::leds::fill(0, 0, 0);
    render_app(idx);
}

void on_button(int btn)
{
    if (badge_switcher_event(&g_sw, btn)) {
        launch(g_sw.active);
    } else if (g_sw.selecting) {
        show_leds();
    }
}

int app_index_by_name(const char *name)
{
    for (int i = 0; i < kAppCount; i++) {
        if (strcmp(kApps[i].name, name) == 0) return i;
    }
    return -1;
}

void on_line(const char *line)
{
    if (line[0] == '@') {
        if (strncmp(line, "@btn ", 5) == 0) {
            const char *b = line + 5;
            if (!strcmp(b, "menu_minus")) on_button(BADGE_BTN_MENU_MINUS);
            else if (!strcmp(b, "menu_plus")) on_button(BADGE_BTN_MENU_PLUS);
            else if (!strcmp(b, "a")) on_button(BADGE_BTN_A);
            else if (!strcmp(b, "b")) on_button(BADGE_BTN_B);
            else ESP_LOGW(TAG, "unknown @btn %s", b);
        } else if (strncmp(line, "@app ", 5) == 0) {
            int i = app_index_by_name(line + 5);
            if (i >= 0) launch(i);
            else ESP_LOGW(TAG, "unknown @app %s", line + 5);
        }
    } else if (!g_sw.selecting && strcmp(kApps[g_sw.active].name, "terminal") == 0) {
        /* Terminal app echoes console lines to the screen (and serial). */
        printf("TERM:%s\n", line);
        ESP_LOGI(TAG, "term: %s", line);
        render_app(g_sw.active);
    }
}

#if CONFIG_BADGE_EMULATOR
void console_task(void *)
{
    uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    ESP_LOGI(TAG, "EMU_READY");   /* contract item 5: console is reading input */
    char line[256];
    size_t n = 0;
    for (;;) {
        uint8_t c;
        if (uart_read_bytes(UART_NUM_0, &c, 1, portMAX_DELAY) != 1) continue;
        if (c == '\r' || c == '\n') {
            if (n) { line[n] = 0; on_line(line); n = 0; }
        } else if (n < sizeof(line) - 1) {
            line[n++] = (char)c;
        }
    }
}
#endif

esp_err_t mount_storage()
{
    esp_vfs_littlefs_conf_t fs = {};
    fs.base_path = "/data";
    fs.partition_label = "storage";
    fs.format_if_mount_failed = false;   /* never auto-format a user's badge (issue #9) */
    fs.dont_mount = false;
    esp_err_t e = esp_vfs_littlefs_register(&fs);
    if (e == ESP_OK) {
        size_t t = 0, u = 0;
        esp_littlefs_info("storage", &t, &u);
        ESP_LOGI(TAG, "LittleFS mounted: %u/%u bytes used", (unsigned)u, (unsigned)t);
    } else {
        ESP_LOGE(TAG, "LittleFS mount failed: %s", esp_err_to_name(e));
    }
    return e;
}

}  // namespace

extern "C" void app_main(void)
{
    ESP_LOGI(TAG, "SuperconX Billboard — native firmware boot (no LVGL)");

    esp_err_t nvs = nvs_flash_init();
    if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }

    size_t psram_before = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    mount_storage();
    ESP_ERROR_CHECK_WITHOUT_ABORT(epd_init());
    epd_clear();
    ESP_ERROR_CHECK_WITHOUT_ABORT(badge::leds::initialize());

    /* Memory preflight: the two 80640-byte planes must have come out of PSRAM,
     * and there must be ample headroom left for the LLM model + app buffers. */
    size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    ESP_LOGI(TAG, "psram_free=%u bytes; planes=2x%u (black=%p red=%p)",
             (unsigned)psram_free, (unsigned)EPD_PLANE_BYTES,
             (void *)epd_black_plane(), (void *)epd_red_plane());
    if (!epd_black_plane() || !epd_red_plane()) {
        ESP_LOGE(TAG, "NO_MEM: planes not allocated");
    } else if (psram_before && psram_before - psram_free < 2 * EPD_PLANE_BYTES) {
        ESP_LOGW(TAG, "planes do not appear to be in PSRAM (freed delta too small)");
    }

    badge_switcher_init(&g_sw, kAppCount, 0);
    show_leds();

#if CONFIG_BADGE_EMULATOR
    xTaskCreate(console_task, "console", 4096, nullptr, 5, nullptr);
#else
    /* Hardware button GPIO scanning arrives with issue #5; mark ready regardless. */
    ESP_LOGI(TAG, "EMU_READY");
#endif

    while (true) {
        ESP_LOGI(TAG, "heartbeat");
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}
