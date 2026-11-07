/*
 * board.h — Billboard Badge (Supercon 10) pin map and board constants.
 *
 * Transcribed from the stock MicroPython firmware (badge_fs/lib/board.py and
 * badge_fs/lib/epd.py) and the hardware teardown in BADGE_REPORT.md.
 *
 * SoC: ESP32-S3, 8 MB quad flash, 8 MB octal PSRAM, native USB-Serial/JTAG.
 */
#pragma once

#include "driver/gpio.h"

/* ---- E-paper display (Pervasive Displays 9.69" 960x672 red/black/white) ---- */
/* Dual driver halves (two CoGs): CS1 = left half, CS2 = right half.           */
#define EPD_PIN_CS1      GPIO_NUM_13   /* chip select, left half               */
#define EPD_PIN_CS2      GPIO_NUM_3    /* chip select, right half              */
#define EPD_PIN_BUSY1    GPIO_NUM_12   /* busy, left half  (input)             */
#define EPD_PIN_BUSY2    GPIO_NUM_8    /* busy, right half (input)             */
#define EPD_PIN_DC       GPIO_NUM_10   /* data(high)/command(low)              */
#define EPD_PIN_RST      GPIO_NUM_11   /* reset                                */
#define EPD_PIN_SCK      GPIO_NUM_21   /* SoftSPI clock                        */
#define EPD_PIN_MOSI     GPIO_NUM_47   /* SoftSPI data out                     */
#define EPD_PIN_MISO     GPIO_NUM_45   /* not connected; signal unused         */
#define EPD_PIN_PWR_EN   GPIO_NUM_48   /* panel power enable                   */
#define EPD_SPI_HZ       8000000       /* 8 MHz per stock SoftSPI baudrate     */

/* ---- Buttons (active low, see badge_fs/lib/button.py) ---- */
/* NOTE: Menu- / Menu+ silkscreen is swapped vs. GPIO per the source comment.  */
#define BTN_PIN_MENU_MINUS GPIO_NUM_40
#define BTN_PIN_MENU_PLUS  GPIO_NUM_15
#define BTN_PIN_USER_A     GPIO_NUM_39
#define BTN_PIN_USER_B     GPIO_NUM_16
#define BTN_PIN_BOOT       GPIO_NUM_0

/* ---- WS2812 LED bar (8 LEDs, menu position indicator) ---- */
#define LED_BAR_PIN        GPIO_NUM_38
#define LED_BAR_COUNT      8

/* ---- Status LED (active low: drive low to light) ---- */
#define STATUS_LED_PIN     GPIO_NUM_1

/* ---- Battery / regulator-input sense (ADC, 1/2 divider, 11 dB atten) ---- */
#define BATT_V_SNS_PIN     GPIO_NUM_14

/* ---- SAO (3 ports) share I2C0 @ 400 kHz ---- */
#define SAO_I2C_SCL        GPIO_NUM_18
#define SAO_I2C_SDA        GPIO_NUM_17
#define SAO_I2C_HZ         400000
#define SAO1_GPIO_A        GPIO_NUM_2
#define SAO1_GPIO_B        GPIO_NUM_42
#define SAO2_GPIO_A        GPIO_NUM_4
#define SAO2_GPIO_B        GPIO_NUM_5
#define SAO3_GPIO_A        GPIO_NUM_7
#define SAO3_GPIO_B        GPIO_NUM_6

/* ---- Test points ---- */
#define TP8_PIN            GPIO_NUM_9
#define TP9_PIN            GPIO_NUM_41

/* ---- Display geometry (see epd.h for framebuffer details) ---- */
#define DISPLAY_WIDTH      960
#define DISPLAY_HEIGHT     672
