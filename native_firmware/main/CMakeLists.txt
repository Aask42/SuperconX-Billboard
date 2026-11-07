# No-LVGL firmware: direct-framebuffer e-paper (epd component) + badge_core logic.
idf_component_register(
    SRCS
        "app_main.cpp"
        "badge_leds.cpp"
        "tinystories_adapter.cpp"
    INCLUDE_DIRS "include"
    REQUIRES
        badge_core epd png
        driver esp_driver_spi esp_driver_uart esp_timer esp_psram
        nvs_flash spi_flash vfs
        littlefs led_strip
)
