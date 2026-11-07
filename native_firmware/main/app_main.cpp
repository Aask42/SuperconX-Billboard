/*
 * app_main.cpp — SuperconX Billboard native firmware (no LVGL, direct framebuffer).
 *
 * Draws straight into the e-paper driver's two 1-bpp planes (epd.h) and drives a
 * button-based app switcher (badge_core state machine + LED menu bar). Implements
 * the emulator contract (T55) so the QEMU harness / behave specs can drive it:
 *   - epd exposes badge_emu_fb_black/red and logs EMU_EPD_REFRESH <n> per refresh
 *   - badge_leds logs EMU_LED lines
 *   - EMU_READY is printed once the console is reading input
 *   - a UART line "@btn <name>" injects a button; "@app <name>" launches an app;
 *     "@name <text>" sets the nametag string; "@gallery <n>" selects a gallery
 *     image (both re-render if their app is active); "@boot <n>" renders the boot
 *     image (splash/egg) for counter n (issue #3 test hook)
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
#include "nvs.h"
#include "esp_littlefs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern "C" {
#include "epd.h"
#include "badge_core.h"
#include "badge_png.h"
}
#include <cstdlib>
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

/* Schedule/Workshops read the real CSV through the badge_core C parser (issue #7).
 * Text rendering of the rows needs the font work (issue #6); for now we parse and
 * log so the data path is exercised and testable. */
void maybe_load_schedule(int idx)
{
    const char *n = kApps[idx].name;
    bool is_sched = (strcmp(n, "schedule") == 0);
    bool is_work = (strcmp(n, "workshops") == 0);
    if (!is_sched && !is_work) return;

    FILE *f = fopen("/data/schedule.csv", "rb");
    if (!f) { ESP_LOGW(TAG, "schedule.csv not found"); return; }
    static char buf[8192];
    size_t r = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[r] = 0;

    static badge_talk_t talks[128];
    int cnt = badge_schedule_parse(buf, talks, 128);
    int shown = 0;
    const char *first = "";
    for (int i = 0; i < cnt; i++) {
        bool talk = badge_schedule_is_talk(talks[i].stage);
        if (talk == is_sched) { if (!shown) first = talks[i].talk; shown++; }
    }
    ESP_LOGI(TAG, "schedule: parsed %d rows, %d for %s; first=\"%s\"", cnt, shown, n, first);
}

void draw_border()
{
    fill_rect_black(0, 0, EPD_WIDTH, 8);
    fill_rect_black(0, EPD_HEIGHT - 8, EPD_WIDTH, 8);
    fill_rect_black(0, 0, 8, EPD_HEIGHT);
    fill_rect_black(EPD_WIDTH - 8, 0, 8, EPD_HEIGHT);
}

/* Draw `s` centered horizontally at row `y` in colour `cls` at `scale` (issue #6). */
void draw_centered(const char *s, int y, int cls, int scale)
{
    uint8_t *blk = epd_black_plane();
    uint8_t *red = epd_red_plane();
    int w = badge_text_width(s, scale);
    int x = (EPD_WIDTH - w) / 2;
    if (x < 0) x = 0;
    badge_draw_text(blk, red, x, y, s, cls, scale);
}

/* Nametag (issue #6): big centered name in black, event subtitle in red.
 * Mutable so it can be set at runtime (and via `@name <text>` under the emulator). */
char g_nametag[32] = "HACKER";   /* default until the user sets one */

void render_nametag()
{
    epd_clear();
    draw_border();
    draw_centered(g_nametag, 230, BADGE_PX_BLACK, 8);   /* 64px-tall glyphs */
    draw_centered("SUPERCON 10", 420, BADGE_PX_RED, 4); /* 32px red subtitle */
    epd_refresh();
    ESP_LOGI(TAG, "nametag rendered: \"%s\"", g_nametag);
}

/* Decode a PNG file straight into the (cleared) planes and refresh. Returns
 * false on missing file / OOM / decode error (planes left cleared, no refresh);
 * the ~w*h*4 RGBA buffer uses PSRAM (CONFIG_SPIRAM_USE_MALLOC=y). Shared by the
 * Gallery (#6) and the boot splash/egg (#3). */
bool render_png_file(const char *path, int *out_w, int *out_h)
{
    FILE *f = fopen(path, "rb");
    if (!f) { ESP_LOGW(TAG, "png: %s not found", path); return false; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (len <= 0) { fclose(f); return false; }
    uint8_t *buf = (uint8_t *)malloc((size_t)len);
    if (!buf) { fclose(f); ESP_LOGE(TAG, "png: OOM reading %s", path); return false; }
    size_t got = fread(buf, 1, (size_t)len, f);
    fclose(f);

    epd_clear();
    int w = 0, h = 0;
    unsigned err = badge_png_decode_to_planes(buf, got,
                                              epd_black_plane(), epd_red_plane(), &w, &h);
    free(buf);
    if (err) { ESP_LOGE(TAG, "png: decode %s failed (%u)", path, err); return false; }
    epd_refresh();
    if (out_w) *out_w = w;
    if (out_h) *out_h = h;
    return true;
}

/* Gallery (issue #6): decode /data/gallery/NNN.png straight into the planes. */
constexpr int kGalleryCount = 44;   /* 001.png .. 044.png (badge_spec GALLERY_COUNT) */
int g_gallery_idx = 1;              /* 1-based currently-shown image */

void render_gallery()
{
    char path[48];
    snprintf(path, sizeof(path), "/data/gallery/%03d.png", g_gallery_idx);
    int w = 0, h = 0;
    if (render_png_file(path, &w, &h)) {
        ESP_LOGI(TAG, "gallery rendered: %s (%dx%d)", path, w, h);
    } else {
        epd_clear();
        draw_border();
        fill_rect_black(430, 286, 100, 100);
        epd_refresh();
    }
}

/* Boot splash / easter egg (issue #3). badge_boot_image(): 1 = splash, 2 = egg.
 * Filenames match badge_spec (SPLASH_IMAGE / EGG_IMAGE). Returns true if drawn. */
bool render_boot_image(int which)
{
    const char *name = (which == 2) ? "never_epd.png" : "supercon-x-splash.png";
    char path[56];
    snprintf(path, sizeof(path), "/data/graphics/%s", name);
    int w = 0, h = 0;
    if (render_png_file(path, &w, &h)) {
        ESP_LOGI(TAG, "boot_image: %s %s (%dx%d)",
                 which == 2 ? "egg" : "splash", path, w, h);
        return true;
    }
    ESP_LOGW(TAG, "boot_image: %s render failed", which == 2 ? "egg" : "splash");
    return false;
}

/* Placeholder render for apps not yet built out (workshops, terminal, LLM):
 * a border plus a per-app block so the screen is non-blank and distinguishable. */
void render_app(int idx)
{
    maybe_load_schedule(idx);
    if (strcmp(kApps[idx].name, "nametag") == 0) { render_nametag(); return; }
    if (strcmp(kApps[idx].name, "gallery") == 0) { render_gallery(); return; }
    epd_clear();
    draw_border();
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
        } else if (strncmp(line, "@name ", 6) == 0) {
            snprintf(g_nametag, sizeof(g_nametag), "%s", line + 6);
            ESP_LOGI(TAG, "nametag set: \"%s\"", g_nametag);
            if (!g_sw.selecting && app_index_by_name("nametag") == g_sw.active)
                render_app(g_sw.active);
        } else if (strncmp(line, "@gallery ", 9) == 0) {
            int n = atoi(line + 9);
            if (n >= 1 && n <= kGalleryCount) {
                g_gallery_idx = n;
                ESP_LOGI(TAG, "gallery index set: %d", n);
                if (!g_sw.selecting && app_index_by_name("gallery") == g_sw.active)
                    render_app(g_sw.active);
            } else {
                ESP_LOGW(TAG, "gallery index %d out of 1..%d", n, kGalleryCount);
            }
        } else if (strncmp(line, "@boot ", 6) == 0) {
            /* Test hook: render the boot image for an arbitrary counter so the
             * splash/egg decision + rendering can be checked deterministically. */
            int n = atoi(line + 6);
            int bi = badge_boot_image(n);
            ESP_LOGI(TAG, "boot_image query n=%d -> %d", n, bi);
            if (bi) {
                render_boot_image(bi);
            } else {
                epd_clear();
                draw_border();
                epd_refresh();
                ESP_LOGI(TAG, "boot_image: none");
            }
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

/* Boot flow (issue #3): read + increment the persistent load counter in NVS,
 * returning the new count. badge_next_load_counter is the pure badge_core rule
 * (first boot -> 1, then +1 each boot). Survives reboots via NVS. */
int read_and_bump_boot_counter()
{
    nvs_handle_t h;
    if (nvs_open("badge", NVS_READWRITE, &h) != ESP_OK)
        return 1;   /* no store -> treat as first boot */
    int32_t cur = 0;
    bool has = (nvs_get_i32(h, "load_counter", &cur) == ESP_OK);
    int next = badge_next_load_counter(has ? 1 : 0, (int)cur);
    nvs_set_i32(h, "load_counter", (int32_t)next);
    nvs_commit(h);
    nvs_close(h);
    return next;
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

    /* Boot flow (issue #3): bump the persistent counter and paint the splash on
     * the early boots / the easter egg on boot #42, before handing to the apps. */
    int boot_counter = read_and_bump_boot_counter();
    ESP_LOGI(TAG, "boot #%d", boot_counter);
    int boot_img = badge_boot_image(boot_counter);
    if (boot_img) render_boot_image(boot_img);

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
