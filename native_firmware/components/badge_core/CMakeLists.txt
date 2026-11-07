# Pure badge logic, no ESP deps — also host-compiled by tests/unit/test_c_core.py.
idf_component_register(SRCS "src/epd_pack.c" "src/led_menu.c" "src/config.c" "src/switcher.c" "src/schedule.c" "src/font.c"
                       INCLUDE_DIRS "include")
