#include "epd_panel.hpp"

#include <algorithm>
#include <array>
#include <cstring>
#include <initializer_list>

#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_check.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

namespace badge::epd {
namespace {

constexpr char kTag[] = "badge_epd";
constexpr int kWidth = 960;
constexpr int kHeight = 672;
constexpr int kHalfWidthBytes = kWidth / 16;
constexpr size_t kHalfPlaneBytes = static_cast<size_t>(kHalfWidthBytes) * kHeight;
constexpr gpio_num_t kChipSelectLeft = GPIO_NUM_13;
constexpr gpio_num_t kChipSelectRight = GPIO_NUM_3;
constexpr gpio_num_t kBusyLeft = GPIO_NUM_12;
constexpr gpio_num_t kBusyRight = GPIO_NUM_8;
constexpr gpio_num_t kDataCommand = GPIO_NUM_10;
constexpr gpio_num_t kReset = GPIO_NUM_11;
constexpr gpio_num_t kClock = GPIO_NUM_21;
constexpr gpio_num_t kMosi = GPIO_NUM_47;
constexpr gpio_num_t kMiso = GPIO_NUM_45;

enum class Target : uint8_t { Both, Left, Right };

#if !CONFIG_BADGE_EMULATOR
uint8_t *s_black_left;
uint8_t *s_black_right;
uint8_t *s_red_left;
uint8_t *s_red_right;
uint8_t *s_tx_black_left;
uint8_t *s_tx_black_right;
uint8_t *s_tx_red_left;
uint8_t *s_tx_red_right;
spi_device_handle_t s_spi;
SemaphoreHandle_t s_frame_mutex;
TaskHandle_t s_refresh_task;

void select(Target target, bool active)
{
    const uint32_t level = active ? 0 : 1;
    if (target != Target::Right) {
        gpio_set_level(kChipSelectLeft, level);
    }
    if (target != Target::Left) {
        gpio_set_level(kChipSelectRight, level);
    }
}

esp_err_t transmit(const uint8_t *data, size_t length, bool reverse_bits = false)
{
    std::array<uint8_t, 256> reversed{};
    while (length > 0) {
        const size_t count = std::min(length, reversed.size());
        const uint8_t *block = data;
        if (reverse_bits) {
            for (size_t i = 0; i < count; ++i) {
                uint8_t value = data[i];
                value = static_cast<uint8_t>((value >> 4) | (value << 4));
                value = static_cast<uint8_t>(((value & 0xCC) >> 2) | ((value & 0x33) << 2));
                reversed[i] = static_cast<uint8_t>(((value & 0xAA) >> 1) | ((value & 0x55) << 1));
            }
            block = reversed.data();
        }
        spi_transaction_t transaction{};
        transaction.length = count * 8;
        transaction.tx_buffer = block;
        ESP_RETURN_ON_ERROR(spi_device_polling_transmit(s_spi, &transaction), kTag, "SPI transfer failed");
        data += count;
        length -= count;
    }
    return ESP_OK;
}

esp_err_t command(Target target, uint8_t index, const uint8_t *data, size_t length,
                  bool reverse_bits = false)
{
    ESP_RETURN_ON_ERROR(gpio_set_level(kDataCommand, 0), kTag, "Cannot set command mode");
    select(target, true);
    const esp_err_t command_result = transmit(&index, 1);
    select(target, false);
    ESP_RETURN_ON_ERROR(command_result, kTag, "Command transfer failed");

    if (length == 0) {
        return ESP_OK;
    }
    ESP_RETURN_ON_ERROR(gpio_set_level(kDataCommand, 1), kTag, "Cannot set data mode");
    select(target, true);
    const esp_err_t data_result = transmit(data, length, reverse_bits);
    select(target, false);
    return data_result;
}

esp_err_t command_both(uint8_t index, std::initializer_list<uint8_t> data)
{
    return command(Target::Both, index, data.begin(), data.size());
}

esp_err_t command_left(uint8_t index, std::initializer_list<uint8_t> data)
{
    return command(Target::Left, index, data.begin(), data.size());
}

esp_err_t command_right(uint8_t index, std::initializer_list<uint8_t> data)
{
    return command(Target::Right, index, data.begin(), data.size());
}

esp_err_t wait_ready(TickType_t timeout)
{
    const TickType_t start = xTaskGetTickCount();
    while (gpio_get_level(kBusyLeft) == 0 || gpio_get_level(kBusyRight) == 0) {
        if (xTaskGetTickCount() - start > timeout) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
    return ESP_OK;
}

esp_err_t reset_panel()
{
    vTaskDelay(pdMS_TO_TICKS(200));
    gpio_set_level(kReset, 1);
    vTaskDelay(pdMS_TO_TICKS(20));
    gpio_set_level(kReset, 0);
    vTaskDelay(pdMS_TO_TICKS(200));
    gpio_set_level(kReset, 1);
    vTaskDelay(pdMS_TO_TICKS(200));
    gpio_set_level(kChipSelectLeft, 1);
    gpio_set_level(kChipSelectRight, 1);
    vTaskDelay(pdMS_TO_TICKS(5));
    return ESP_OK;
}

esp_err_t initialize_cog()
{
    ESP_RETURN_ON_ERROR(command_both(0x05, {0x7D}), kTag, "CoG reset failed");
    vTaskDelay(pdMS_TO_TICKS(200));
    ESP_RETURN_ON_ERROR(command_both(0x05, {0x00}), kTag, "CoG reset failed");
    vTaskDelay(pdMS_TO_TICKS(10));
    ESP_RETURN_ON_ERROR(command_both(0xD8, {0x80}), kTag, "CoG sync failed");
    ESP_RETURN_ON_ERROR(command_both(0xD6, {0x00}), kTag, "CoG setup failed");
    ESP_RETURN_ON_ERROR(command_both(0xA7, {0x10}), kTag, "CoG setup failed");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(command_both(0xA7, {0x00}), kTag, "CoG setup failed");
    vTaskDelay(pdMS_TO_TICKS(100));
    ESP_RETURN_ON_ERROR(command_both(0x03, {0x00, 0x11}), kTag, "CoG oscillator setup failed");

    for (Target target : {Target::Left, Target::Right}) {
        esp_err_t (*send)(uint8_t, std::initializer_list<uint8_t>) =
            target == Target::Left ? command_left : command_right;
        ESP_RETURN_ON_ERROR(send(0x44, {0x00}), kTag, "Panel window setup failed");
        ESP_RETURN_ON_ERROR(send(0x45, {0x80}), kTag, "Panel window setup failed");
        ESP_RETURN_ON_ERROR(send(0xA7, {0x10}), kTag, "Panel setup failed");
        vTaskDelay(pdMS_TO_TICKS(100));
        ESP_RETURN_ON_ERROR(send(0xA7, {0x00}), kTag, "Panel setup failed");
        vTaskDelay(pdMS_TO_TICKS(100));
        ESP_RETURN_ON_ERROR(send(0x44, {0x06}), kTag, "Panel window setup failed");
        ESP_RETURN_ON_ERROR(send(0x45, {0x82}), kTag, "Panel temperature setup failed");
        ESP_RETURN_ON_ERROR(send(0xA7, {0x10}), kTag, "Panel setup failed");
        vTaskDelay(pdMS_TO_TICKS(100));
        ESP_RETURN_ON_ERROR(send(0xA7, {0x00}), kTag, "Panel setup failed");
        vTaskDelay(pdMS_TO_TICKS(100));
    }

    ESP_RETURN_ON_ERROR(command_both(0x60, {0x25}), kTag, "Panel timing setup failed");
    ESP_RETURN_ON_ERROR(command_left(0x61, {0x01}), kTag, "Panel direction setup failed");
    return command_both(0x02, {0x00});
}

esp_err_t send_images(const uint8_t *black_left, const uint8_t *red_left,
                     const uint8_t *black_right, const uint8_t *red_right)
{
    const std::initializer_list<uint8_t> duw = {0x00, 0x3B, 0x00, 0x00, 0x9F, 0x02};
    const std::initializer_list<uint8_t> drfw = {0x00, 0x3B, 0x00, 0xA9};
    const std::array<uint8_t, 3> ram = {0x3B, 0x00, 0x14};
    ESP_RETURN_ON_ERROR(command(Target::Both, 0x13, duw.begin(), duw.size()), kTag, "Panel window failed");
    ESP_RETURN_ON_ERROR(command(Target::Both, 0x90, drfw.begin(), drfw.size()), kTag, "Panel window failed");
    ESP_RETURN_ON_ERROR(command(Target::Left, 0x12, ram.data(), ram.size()), kTag, "Left RAM setup failed");
    ESP_RETURN_ON_ERROR(command(Target::Left, 0x10, black_left, kHalfPlaneBytes, true), kTag, "Left black plane failed");
    ESP_RETURN_ON_ERROR(command(Target::Left, 0x12, ram.data(), ram.size()), kTag, "Left RAM setup failed");
    ESP_RETURN_ON_ERROR(command(Target::Left, 0x11, red_left, kHalfPlaneBytes, true), kTag, "Left red plane failed");
    ESP_RETURN_ON_ERROR(command(Target::Right, 0x12, ram.data(), ram.size()), kTag, "Right RAM setup failed");
    ESP_RETURN_ON_ERROR(command(Target::Right, 0x10, black_right, kHalfPlaneBytes, true), kTag, "Right black plane failed");
    ESP_RETURN_ON_ERROR(command(Target::Right, 0x12, ram.data(), ram.size()), kTag, "Right RAM setup failed");
    return command(Target::Right, 0x11, red_right, kHalfPlaneBytes, true);
}

esp_err_t dcdc_start()
{
    ESP_RETURN_ON_ERROR(command_both(0x51, {0x50, 0x01}), kTag, "DCDC start failed");
    for (uint8_t value = 1; value <= 4; ++value) {
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x1F}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x51, {0x50, value}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x9F}), kTag, "DCDC start failed");
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    for (uint8_t value = 1; value <= 10; ++value) {
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x1F}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x51, {0x0A, value}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x9F}), kTag, "DCDC start failed");
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    for (uint8_t value = 3; value <= 10; ++value) {
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x7F}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x51, {0x0A, value}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x09, {0xFF}), kTag, "DCDC start failed");
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    for (int value = 9; value >= 2; --value) {
        ESP_RETURN_ON_ERROR(command_both(0x09, {0x7F}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x51, {static_cast<uint8_t>(value), 0x0A}), kTag, "DCDC start failed");
        ESP_RETURN_ON_ERROR(command_both(0x09, {0xFF}), kTag, "DCDC start failed");
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    ESP_RETURN_ON_ERROR(command_both(0x09, {0xFF}), kTag, "DCDC start failed");
    vTaskDelay(pdMS_TO_TICKS(10));
    return ESP_OK;
}

esp_err_t update_panel()
{
    ESP_RETURN_ON_ERROR(reset_panel(), kTag, "Panel reset failed");
    ESP_RETURN_ON_ERROR(wait_ready(pdMS_TO_TICKS(30000)), kTag, "Panel busy timeout");
    ESP_RETURN_ON_FALSE(xSemaphoreTake(s_frame_mutex, portMAX_DELAY) == pdTRUE,
                        ESP_FAIL, kTag, "Frame lock failed");
    std::memcpy(s_tx_black_left, s_black_left, kHalfPlaneBytes);
    std::memcpy(s_tx_black_right, s_black_right, kHalfPlaneBytes);
    std::memcpy(s_tx_red_left, s_red_left, kHalfPlaneBytes);
    std::memcpy(s_tx_red_right, s_red_right, kHalfPlaneBytes);
    xSemaphoreGive(s_frame_mutex);

    ESP_RETURN_ON_ERROR(send_images(s_tx_black_left, s_tx_red_left, s_tx_black_right, s_tx_red_right),
                        kTag, "Image transfer failed");
    ESP_RETURN_ON_ERROR(initialize_cog(), kTag, "CoG initialization failed");
    ESP_RETURN_ON_ERROR(dcdc_start(), kTag, "DCDC start failed");
    ESP_RETURN_ON_ERROR(wait_ready(pdMS_TO_TICKS(30000)), kTag, "Panel busy timeout");
    ESP_RETURN_ON_ERROR(command_both(0x15, {0x3C}), kTag, "Panel refresh command failed");
    vTaskDelay(pdMS_TO_TICKS(5));

    ESP_RETURN_ON_ERROR(wait_ready(pdMS_TO_TICKS(30000)), kTag, "Panel refresh timeout");
    ESP_RETURN_ON_ERROR(command_both(0x09, {0x7F}), kTag, "DCDC shutdown failed");
    ESP_RETURN_ON_ERROR(command_both(0x05, {0x7D}), kTag, "DCDC shutdown failed");
    ESP_RETURN_ON_ERROR(command_both(0x09, {0x00}), kTag, "DCDC shutdown failed");
    vTaskDelay(pdMS_TO_TICKS(200));
    ESP_RETURN_ON_ERROR(wait_ready(pdMS_TO_TICKS(30000)), kTag, "Panel shutdown timeout");
    gpio_set_level(kDataCommand, 0);
    gpio_set_level(kChipSelectLeft, 0);
    gpio_set_level(kChipSelectRight, 0);
    gpio_set_level(kReset, 0);
    gpio_set_level(kChipSelectLeft, 1);
    gpio_set_level(kChipSelectRight, 1);
    gpio_set_level(GPIO_NUM_48, 0);
    ESP_LOGI(kTag, "Full panel refresh completed");
    return ESP_OK;
}

void refresh_task(void *)
{
    while (true) {
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
        vTaskDelay(pdMS_TO_TICKS(100));
        while (ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(100)) > 0) {
        }
        const esp_err_t result = update_panel();
        if (result != ESP_OK) {
            ESP_LOGE(kTag, "Panel refresh failed: %s", esp_err_to_name(result));
        }
    }
}
#endif

}  // namespace

esp_err_t initialize()
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "QEMU display backend selected");
    return ESP_OK;
#else
    constexpr std::array<gpio_num_t, 6> output_pins = {
        kChipSelectLeft, kChipSelectRight, kDataCommand, kReset,
        GPIO_NUM_48, kClock,
    };
    for (gpio_num_t pin : output_pins) {
        gpio_config_t config{};
        config.pin_bit_mask = 1ULL << pin;
        config.mode = GPIO_MODE_OUTPUT;
        config.pull_up_en = GPIO_PULLUP_DISABLE;
        config.pull_down_en = GPIO_PULLDOWN_DISABLE;
        config.intr_type = GPIO_INTR_DISABLE;
        ESP_RETURN_ON_ERROR(gpio_config(&config), kTag, "Panel GPIO setup failed");
    }
    for (gpio_num_t pin : {kBusyLeft, kBusyRight}) {
        gpio_config_t config{};
        config.pin_bit_mask = 1ULL << pin;
        config.mode = GPIO_MODE_INPUT;
        config.pull_up_en = GPIO_PULLUP_DISABLE;
        config.pull_down_en = GPIO_PULLDOWN_DISABLE;
        config.intr_type = GPIO_INTR_DISABLE;
        ESP_RETURN_ON_ERROR(gpio_config(&config), kTag, "Panel busy GPIO setup failed");
    }
    gpio_set_level(kChipSelectLeft, 1);
    gpio_set_level(kChipSelectRight, 1);
    gpio_set_level(kDataCommand, 1);
    gpio_set_level(kReset, 1);
    gpio_set_level(GPIO_NUM_48, 1);

    spi_bus_config_t bus_config{};
    bus_config.sclk_io_num = kClock;
    bus_config.mosi_io_num = kMosi;
    bus_config.miso_io_num = kMiso;
    bus_config.quadwp_io_num = -1;
    bus_config.quadhd_io_num = -1;
    bus_config.max_transfer_sz = 4096;
    ESP_RETURN_ON_ERROR(spi_bus_initialize(SPI2_HOST, &bus_config, SPI_DMA_CH_AUTO), kTag, "Panel SPI bus init failed");
    spi_device_interface_config_t device_config{};
    device_config.clock_speed_hz = 8 * 1000 * 1000;
    device_config.mode = 0;
    device_config.spics_io_num = -1;
    device_config.queue_size = 1;
    ESP_RETURN_ON_ERROR(spi_bus_add_device(SPI2_HOST, &device_config, &s_spi), kTag, "Panel SPI device init failed");

    auto allocate_plane = []() {
        return static_cast<uint8_t *>(heap_caps_calloc(kHalfPlaneBytes, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    };
    s_black_left = allocate_plane();
    s_black_right = allocate_plane();
    s_red_left = allocate_plane();
    s_red_right = allocate_plane();
    s_tx_black_left = allocate_plane();
    s_tx_black_right = allocate_plane();
    s_tx_red_left = allocate_plane();
    s_tx_red_right = allocate_plane();
    ESP_RETURN_ON_FALSE(
        s_black_left && s_black_right && s_red_left && s_red_right &&
            s_tx_black_left && s_tx_black_right && s_tx_red_left && s_tx_red_right,
        ESP_ERR_NO_MEM, kTag, "Panel framebuffer allocation failed");
    s_frame_mutex = xSemaphoreCreateMutex();
    ESP_RETURN_ON_FALSE(s_frame_mutex != nullptr, ESP_ERR_NO_MEM, kTag, "Panel mutex allocation failed");
    ESP_RETURN_ON_FALSE(
        xTaskCreate(refresh_task, "epd_refresh", 8192, nullptr, 3, &s_refresh_task) == pdPASS,
        ESP_ERR_NO_MEM, kTag, "Panel task creation failed");
    return ESP_OK;
#endif
}

esp_err_t write_region(const lv_area_t *area, const uint16_t *rgb565)
{
#if CONFIG_BADGE_EMULATOR
    (void)area;
    (void)rgb565;
    return ESP_OK;
#else
    ESP_RETURN_ON_FALSE(area != nullptr && rgb565 != nullptr, ESP_ERR_INVALID_ARG, kTag, "Invalid display region");
    ESP_RETURN_ON_FALSE(area->x1 >= 0 && area->y1 >= 0 &&
                            area->x2 < kWidth && area->y2 < kHeight,
                        ESP_ERR_INVALID_ARG, kTag, "Display region out of bounds");
    xSemaphoreTake(s_frame_mutex, portMAX_DELAY);
    const int width = area->x2 - area->x1 + 1;
    for (int y = area->y1; y <= area->y2; ++y) {
        for (int x = area->x1; x <= area->x2; ++x) {
            const uint16_t color = rgb565[(y - area->y1) * width + (x - area->x1)];
            const uint8_t red = static_cast<uint8_t>((color >> 11) & 0x1F);
            const uint8_t green = static_cast<uint8_t>((color >> 5) & 0x3F);
            const uint8_t blue = static_cast<uint8_t>(color & 0x1F);
            uint8_t *black_plane;
            uint8_t *red_plane;
            const int half_x = x < kWidth / 2 ? x : x - kWidth / 2;
            const size_t offset = static_cast<size_t>(y) * kHalfWidthBytes + half_x / 8;
            const uint8_t mask = static_cast<uint8_t>(1U << (7 - (half_x & 7)));
            if (x < kWidth / 2) {
                black_plane = s_black_left;
                red_plane = s_red_left;
            } else {
                black_plane = s_black_right;
                red_plane = s_red_right;
            }
            const bool is_red = (red & 0x10) && !(green & 0x20) && !(blue & 0x10);
            const bool is_black = !(red & 0x10) && !(green & 0x20) && !(blue & 0x10);
            if (is_red) {
                red_plane[offset] |= mask;
                black_plane[offset] &= static_cast<uint8_t>(~mask);
            } else if (is_black) {
                black_plane[offset] |= mask;
                red_plane[offset] &= static_cast<uint8_t>(~mask);
            } else {
                black_plane[offset] &= static_cast<uint8_t>(~mask);
                red_plane[offset] &= static_cast<uint8_t>(~mask);
            }
        }
    }
    xSemaphoreGive(s_frame_mutex);
    return ESP_OK;
#endif
}

void refresh()
{
#if CONFIG_BADGE_EMULATOR
    ESP_LOGI(kTag, "QEMU full refresh requested");
#else
    if (s_refresh_task != nullptr) {
        xTaskNotifyGive(s_refresh_task);
    }
#endif
}

}  // namespace badge::epd
