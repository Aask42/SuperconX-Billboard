@emu @fbdump
Feature: Pre-hardware preflight (memory + display path)
  One boot that proves, before any hardware is connected, that the firmware
  loads into memory as expected and the full draw -> plane -> refresh -> dump
  path produces the right pixels. Kept to a single QEMU boot so it is not
  subject to the multi-boot emulator flakiness.

  Scenario: Memory loads and the display pipeline renders the expected pixels
    Given the badge firmware is running in the emulator
    # ---- memory loads as expected ----
    Then I should see "octal_psram: density" within 30 seconds
    And I should see "LittleFS mounted" within 25 seconds
    And I should see "epd_init: emulator backend" within 25 seconds
    And I should see "psram_free=" within 25 seconds
    And I should see "EMU_READY" within 25 seconds
    And I should not see "NO_MEM"
    And I should not see "alloc failed"
    And I should not see "Guru Meditation"
    # ---- display path renders correct pixels ----
    When I type "@app nametag" on the serial console
    Then at least 1 e-paper refresh has occurred
    And the panel pixel at 4,4 is black
    And the panel pixel at 955,667 is black
    And the panel pixel at 400,400 is white
    And the e-paper screen is saved to "preflight.png"
