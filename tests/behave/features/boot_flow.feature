@emu
Feature: Boot flow — splash and easter egg (issue #3)
  The badge bumps a persistent NVS load counter on every boot and paints the
  splash on the early boots, the easter egg on boot #42, and nothing otherwise.
  (The pure counter/boot-image rules are unit-tested in test_config_bootflow.py;
  these drive the real firmware end to end in QEMU.)

  @fbdump
  Scenario: A fresh boot counts the boot and paints the splash
    # "boot #N" and the splash render are logged BEFORE the console is ready, so
    # use the non-consuming "is running" given and assert in stream order (an
    # expect() for EMU_READY would advance past these earlier lines).
    Given the badge firmware is running in the emulator
    Then I should see "boot #1" within 20 seconds
    And "boot_image: splash" appears within 20 seconds
    And "EMU_READY" appears within 20 seconds
    And at least 1 e-paper refresh has occurred
    And the panel has black ink in rows 0 to 672
    And the e-paper screen is saved to "splash.png"

  @fbdump
  Scenario: The easter egg appears on boot number 42
    Given the badge firmware has booted
    When I type "@boot 42" on the serial console
    Then I should see "boot_image: egg" within 10 seconds
    And the panel has black ink in rows 0 to 672
    And the e-paper screen is saved to "egg.png"

  Scenario: An ordinary boot shows no boot image
    Given the badge firmware has booted
    When I type "@boot 3" on the serial console
    Then I should see "boot_image: none" within 10 seconds
    And I should not see "boot_image: egg"
