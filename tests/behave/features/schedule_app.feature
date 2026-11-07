@emu @wip
Feature: Schedule and Workshops apps
  The Schedule app lays out the talks (Lamarr/Hopper) from /data/schedule.csv per
  day; the Workshops app shows the room sessions from the same file. Tagged wip:
  needs the apps plus the T55 refresh/button logging.

  @fbdump
  Scenario: The Schedule app renders a day of talks
    Given the badge firmware has booted
    When I type "@app schedule" on the serial console
    Then at least 1 e-paper refresh has occurred
    And the e-paper screen is saved to "schedule.png"

  Scenario: Menu+ pages between Saturday and Sunday
    Given the badge firmware has booted
    When I type "@app schedule" on the serial console
    And I press the menu_plus button
    Then at least 2 e-paper refreshes have occurred
