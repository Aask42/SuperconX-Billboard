@emu @wip
Feature: Button-driven app switcher
  Menu-/Menu+ move the selection, User A/B launch the selected app, and each
  launch repaints the e-paper. Mirrors the stock AppSwitcher behaviour.
  (@wip: needs the firmware's emulator button contract + EMU_EPD_REFRESH.)

  Scenario: Entering selection mode and moving the cursor lights the LED bar
    Given the badge firmware has booted
    When I press the menu_plus button
    Then LED 1 is lit
    When I press the menu_plus button
    Then LED 2 is lit

  Scenario: Launching an app refreshes the e-paper
    Given the badge firmware has booted
    When I press the menu_plus button
    And I press the a button
    Then at least 1 e-paper refresh has occurred

  @fbdump
  Scenario: The Nametag app renders to the panel
    Given the badge firmware has booted
    When I press the a button
    Then at least 1 e-paper refresh has occurred
    And the e-paper screen is saved to "nametag.png"
