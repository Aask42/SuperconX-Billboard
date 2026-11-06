@emu
Feature: The badge firmware boots in the emulator
  As a developer, I want the native firmware to reach app_main and bring up its
  core subsystems in QEMU without panicking, so regressions are caught before flashing.

  Scenario: Core boot reaches app_main and mounts storage without a panic
    Given the badge firmware is running in the emulator
    Then I should see "octal_psram: density" within 30 seconds
    And I should see "Calling app_main\(\)" within 30 seconds
    And I should see "SuperconX Billboard" within 20 seconds
    And I should see "LittleFS mounted" within 20 seconds
    And I should not see "Guru Meditation"
    And I should not see "abort\(\) was called"

  Scenario: The firmware finishes init and runs without panicking
    # Kept short and deterministic: a long wait for the 60s heartbeat makes QEMU
    # flaky on CI hosts. emu.Qemu also fails automatically on a reboot loop.
    Given the badge firmware is running in the emulator
    Then I should see "subsystems initialized" within 25 seconds
    When I wait 3 seconds
    Then I should not see "Guru Meditation"
    And I should not see "abort\(\) was called"
