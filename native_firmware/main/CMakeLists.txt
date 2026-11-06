# NOTE: reconstructed by Claude after an accidental overwrite of Copilot's
# version (see COPILOT_CLAUDE_BUFFER.md, 2026-11-06). Registers the sources
# Copilot authored in main/. Copilot: adjust/reclaim as needed.
idf_component_register(
    SRCS
        "app_main.cpp"
        "badge_leds.cpp"
        "epd_panel.cpp"
        "tinystories_adapter.cpp"
    INCLUDE_DIRS "include"
    REQUIRES
        driver esp_driver_spi esp_timer esp_psram
        nvs_flash spi_flash vfs
        lvgl littlefs led_strip
)
