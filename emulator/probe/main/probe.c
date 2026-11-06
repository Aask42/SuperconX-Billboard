/* Emulator self-test firmware: proves PSRAM, UART input, and framebuffer dump. */
#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "driver/uart.h"

#define W 960
#define H 672
#define PLANE (W / 8 * H)

uint8_t *emu_black;
uint8_t *emu_red;

void app_main(void)
{
    ESP_LOGI("probe", "psram free=%u", (unsigned)heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    emu_black = heap_caps_calloc(1, PLANE, MALLOC_CAP_SPIRAM);
    emu_red = heap_caps_calloc(1, PLANE, MALLOC_CAP_SPIRAM);
    if (!emu_black || !emu_red) {
        ESP_LOGE("probe", "PLANES_FAIL");
        return;
    }
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            int i = y * (W / 8) + x / 8;
            uint8_t bit = 0x80 >> (x % 8);
            if (x < 20 || y < 20 || x >= W - 20 || y >= H - 20) emu_black[i] |= bit;
            else if ((x - W / 2) * (x - W / 2) + (y - H / 2) * (y - H / 2) < 150 * 150) emu_red[i] |= bit;
        }
    }
    ESP_LOGI("probe", "PLANES_READY");
    uart_driver_install(UART_NUM_0, 1024, 0, 0, NULL, 0);
    char line[128];
    size_t n = 0;
    for (;;) {
        uint8_t c;
        if (uart_read_bytes(UART_NUM_0, &c, 1, portMAX_DELAY) != 1) continue;
        if (c == '\r' || c == '\n') {
            line[n] = 0;
            if (n) printf("ECHO:%s\n", line);
            n = 0;
        } else if (n < sizeof(line) - 1) {
            line[n++] = (char)c;
        }
    }
}
