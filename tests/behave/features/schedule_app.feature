@emu
Feature: Schedule and Workshops apps
  The Schedule app lays out the talks (Lamarr/Hopper) from /data/schedule.csv per
  day; the Workshops app shows the room sessions from the same file. Tagged wip:
  needs the apps plus the T55 refresh/button logging.

  @fbdump
  Scenario: The Schedule app parses the CSV and renders
    Given the badge firmware has booted
    When I type "@app schedule" on the serial console
    Then I should see "schedule: parsed" within 10 seconds
    And at least 1 e-paper refresh has occurred
    And the e-paper screen is saved to "schedule.png"

  @wip
  Scenario: Menu+ pages between Saturday and Sunday
    # In-app day paging is issue #7 (real Schedule app). Today Menu+ returns to
    # the app menu (faithful to app_switcher.py), so this stays @wip.
    Given the badge firmware has booted
    When I type "@app schedule" on the serial console
    And I press the menu_plus button
    Then at least 2 e-paper refreshes have occurred
