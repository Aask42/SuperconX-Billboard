@emu
Feature: Filesystem safety (never auto-format a user's badge)
  The firmware mounts /data with format_if_mount_failed=false, so a corrupt or
  unexpected filesystem must NOT be silently reformatted — that would wipe a
  user's data. A failed mount should be logged and the badge should keep running.

  Scenario: A normal boot mounts without formatting
    Given the badge firmware is running in the emulator
    Then I should see "LittleFS mounted" within 25 seconds
    And I should not see "formatting"
    And I should not see "partition was formatted"

  Scenario: A corrupt filesystem fails to mount but is not formatted
    Given the badge firmware booted with a corrupted filesystem
    Then I should see "LittleFS mount failed" within 25 seconds
    And I should see "EMU_READY" within 25 seconds
    And I should not see "formatting"
    And I should not see "partition was formatted"
    And I should not see "Guru Meditation"
