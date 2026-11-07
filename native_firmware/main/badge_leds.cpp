/* WS2812 menu LED bar. Colours come from badge_core (ported led_bar.py); under
 * the emulator each update is logged as EMU_LED lines for the QEMU harness. */
#include "badge_leds.hpp"

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"

extern "C" {
#include "badge_core.h"
}

#if !CONFIG_BADGE_EMULATOR
#include "led_strip.h"
#endif

namespace badge::leds {
namespace {

constexpr char kTag[] = "badge_leds";
constexpr size_t kLedCount = 8;
constexpr gpio_num_t kDataPin = GPIO_NUM_38;

#if !CONFIG_BADGE_EMULATOR
led_strip_handle_t s_strip;
inline uint8_t scale(uint8_t v) { return (uint8_t)(v * 40); }  /* palette 0..3 -> brightness */
#endif

}  // namespace

esp_err_t initialize()
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "emulator LED backend (EMU_LED log lines)");
    return ESP_OK;
#else
    led_strip_config_t strip_config{};
    strip_config.strip_gpio_num = kDataPin;
    strip_config.max_leds = kLedCount;
    strip_config.led_pixel_format = LED_PIXEL_FORMAT_GRB;
    strip_config.led_model = LED_MODEL_WS2812;
    led_strip_rmt_config_t rmt_config{};
    rmt_config.clk_src = RMT_CLK_SRC_DEFAULT;
    rmt_config.resolution_hz = 10 * 1000 * 1000;
    rmt_config.mem_block_symbols = 64;
    ESP_RETURN_ON_ERROR(led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip),
                        kTag, "WS2812 init failed");
    return clear();
#endif
}

esp_err_t fill(uint8_t red, uint8_t green, uint8_t blue)
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "EMU_LED fill %u %u %u", red, green, blue);
    return ESP_OK;
#else
    for (size_t i = 0; i < kLedCount; i++) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_strip, i, scale(red), scale(green), scale(blue)),
                            kTag, "set_pixel failed");
    }
    return led_strip_refresh(s_strip);
#endif
}

esp_err_t clear()
{
    return fill(0, 0, 0);
}

esp_err_t show_menu(size_t selected_index, size_t app_count)
{
    uint8_t leds[8][3];
    badge_menu_leds((int)selected_index, (int)app_count, leds);
#if CONFIG_BADGE_EMULATOR
    for (size_t i = 0; i < kLedCount; i++) {
        ESP_LOGI(kTag, "EMU_LED %u %u %u %u", (unsigned)i, leds[i][0], leds[i][1], leds[i][2]);
    }
    return ESP_OK;
#else
    for (size_t i = 0; i < kLedCount; i++) {
        ESP_RETURN_ON_ERROR(led_strip_set_pixel(s_strip, i, scale(leds[i][0]), scale(leds[i][1]), scale(leds[i][2])),
                            kTag, "set_pixel failed");
    }
    return led_strip_refresh(s_strip);
#endif
}

}  // namespace badge::leds
