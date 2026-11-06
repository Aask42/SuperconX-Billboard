#pragma once

#include "esp_err.h"
#include "lvgl.h"

namespace badge::epd {

esp_err_t initialize();
esp_err_t write_region(const lv_area_t *area, const uint16_t *rgb565);
void refresh();

}
