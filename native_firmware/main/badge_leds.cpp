#include "badge_leds.hpp"

#include <algorithm>
#include <array>

#include "driver/gpio.h"
#include "esp_check.h"
#include "esp_log.h"
#if !CONFIG_BADGE_EMULATOR
#include "led_strip.h"
#endif

namespace badge::leds {
namespace {

constexpr char kTag[] = "badge_leds";
constexpr size_t kLedCount = 8;
constexpr gpio_num_t kDataPin = GPIO_NUM_38;
struct Color {
    uint8_t red;
    uint8_t green;
    uint8_t blue;
};
constexpr std::array<Color, 6> kMenuBackgrounds = {{
    {2, 1, 0}, {0, 1, 1}, {2, 0, 1}, {3, 0, 0}, {0, 2, 0}, {0, 0, 1},
}};
constexpr Color kSelected = {3, 2, 1};
#if !CONFIG_BADGE_EMULATOR
led_strip_handle_t s_strip;
#endif

}  // namespace

esp_err_t initialize()
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "QEMU LED backend selected");
    return ESP_OK;
#else
    led_strip_config_t strip_config{};
    strip_config.strip_gpio_num = kDataPin;
    strip_config.max_leds = kLedCount;
    strip_config.led_pixel_format = LED_PIXEL_FORMAT_GRB;
    strip_config.led_model = LED_MODEL_WS2812;
    strip_config.flags.invert_out = false;
    led_strip_rmt_config_t rmt_config{};
    rmt_config.clk_src = RMT_CLK_SRC_DEFAULT;
    rmt_config.resolution_hz = 10 * 1000 * 1000;
    rmt_config.mem_block_symbols = 64;
    rmt_config.flags.with_dma = false;
    ESP_RETURN_ON_ERROR(
        led_strip_new_rmt_device(&strip_config, &rmt_config, &s_strip),
        kTag, "WS2812 strip initialization failed");
    return clear();
#endif
}

esp_err_t fill(uint8_t red, uint8_t green, uint8_t blue)
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "QEMU LED fill (%u,%u,%u)",
             static_cast<unsigned>(red),
             static_cast<unsigned>(green),
             static_cast<unsigned>(blue));
    return ESP_OK;
#else
    ESP_RETURN_ON_FALSE(s_strip != nullptr, ESP_ERR_INVALID_STATE, kTag, "LED strip is not initialized");
    for (size_t i = 0; i < kLedCount; ++i) {
        ESP_RETURN_ON_ERROR(
            led_strip_set_pixel(s_strip, i, red, green, blue),
            kTag, "Could not set LED pixel");
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
    ESP_RETURN_ON_FALSE(app_count > 0 && selected_index < app_count,
                        ESP_ERR_INVALID_ARG, kTag, "Invalid LED menu selection");
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "QEMU LED menu selection %u/%u",
             static_cast<unsigned>(selected_index + 1),
             static_cast<unsigned>(app_count));
    return ESP_OK;
#else
    ESP_RETURN_ON_FALSE(s_strip != nullptr, ESP_ERR_INVALID_STATE, kTag, "LED strip is not initialized");
    const size_t page = selected_index / kLedCount;
    const size_t selected = selected_index % kLedCount;
    const Color background = kMenuBackgrounds[page % kMenuBackgrounds.size()];
    const size_t on_page = std::min(kLedCount, app_count - page * kLedCount);
    for (size_t i = 0; i < kLedCount; ++i) {
        const Color color = i == selected ? kSelected :
                            i < on_page ? background : Color{0, 0, 0};
        ESP_RETURN_ON_ERROR(
            led_strip_set_pixel(s_strip, i, color.red, color.green, color.blue),
            kTag, "Could not set menu LED");
    }
    return led_strip_refresh(s_strip);
#endif
}

}  // namespace badge::leds
