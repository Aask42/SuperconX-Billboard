#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"

namespace badge::leds {

esp_err_t initialize();
esp_err_t clear();
esp_err_t fill(uint8_t red, uint8_t green, uint8_t blue);
esp_err_t show_menu(size_t selected_index, size_t app_count);

}
