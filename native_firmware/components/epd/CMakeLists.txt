idf_component_register(SRCS "src/epd.c"
                       INCLUDE_DIRS "include"
                       REQUIRES driver esp_driver_spi board esp_timer)
