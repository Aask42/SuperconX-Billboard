@emu
Feature: Nametag app
  The Nametag app (issue #6) renders a large centered name in black and a red
  event subtitle, using the badge_core 8x8 font straight into the colour planes.

  @fbdump
  Scenario: Nametag paints the name and subtitle
    Given the badge firmware has booted
    When I type "@app nametag" on the serial console
    When I type "@name CLAUDE" on the serial console
    Then I should see "nametag rendered" within 10 seconds
    And at least 2 e-paper refreshes have occurred
    And the panel has black ink in rows 230 to 294
    And the panel has red ink in rows 420 to 452
    And the e-paper screen is saved to "nametag.png"
