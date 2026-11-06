/*
 * app_main.cpp — SuperconX Billboard native firmware entry point.
 *
 * ⚠️ RECONSTRUCTED by Claude after an accidental overwrite of Copilot's original
 * on 2026-11-06 (see COPILOT_CLAUDE_BUFFER.md). Copilot's version implemented
 * badge::initialize() (declared in badge_app.hpp) and wired up the subsystems;
 * that content was lost. This is a best-effort rebuild against the surviving
 * interface headers so the project links and runs. Copilot: please replace this
 * with your intended app_main / badge::initialize().
 */
#include "badge_app.hpp"

#include <cstdio>

#include "esp_err.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "esp_littlefs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "badge_leds.hpp"
#include "epd_panel.hpp"
#include "tinystories_adapter.hpp"

namespace badge {

static const char *kTag = "badge";

static esp_err_t mount_storage()
{
    esp_vfs_littlefs_conf_t conf = {};
    conf.base_path = "/data";
    conf.partition_label = "storage";
    conf.format_if_mount_failed = false;
    conf.dont_mount = false;
    esp_err_t err = esp_vfs_littlefs_register(&conf);
    if (err != ESP_OK) {
        ESP_LOGE(kTag, "LittleFS mount (/data) failed: %s", esp_err_to_name(err));
    } else {
        size_t total = 0, used = 0;
        esp_littlefs_info("storage", &total, &used);
        ESP_LOGI(kTag, "LittleFS mounted: %u/%u bytes used", (unsigned)used, (unsigned)total);
    }
    return err;
}

esp_err_t initialize()
{
    ESP_LOGW(kTag, "app_main.cpp is a reconstruction after an overwrite — Copilot to restore");

    esp_err_t nvs = nvs_flash_init();
    if (nvs == ESP_ERR_NVS_NO_FREE_PAGES || nvs == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs = nvs_flash_init();
    }
    ESP_ERROR_CHECK_WITHOUT_ABORT(nvs);

    mount_storage();

    ESP_ERROR_CHECK_WITHOUT_ABORT(badge::leds::initialize());
    badge::leds::fill(0, 1, 1);           /* cyan "booting" tint, as the stock splash did */

    ESP_ERROR_CHECK_WITHOUT_ABORT(badge::epd::initialize());

    ESP_LOGI(kTag, "subsystems initialized (leds + epd + storage)");
    return ESP_OK;
}

}  // namespace badge

extern "C" void app_main(void)
{
    ESP_LOGI("main", "SuperconX Billboard — native firmware boot");
    ESP_ERROR_CHECK_WITHOUT_ABORT(badge::initialize());

    while (true) {
        ESP_LOGI("main", "heartbeat");
        vTaskDelay(pdMS_TO_TICKS(60000));
    }
}
