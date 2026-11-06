# Top-level ESP-IDF project for the SuperconX Billboard native firmware.
# Target: ESP32-S3 (8 MB quad flash + 8 MB octal PSRAM), ESP-IDF v5.4.x.
cmake_minimum_required(VERSION 3.16)

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(superconx_billboard)
