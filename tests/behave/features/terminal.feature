@emu
Feature: Serial terminal app
  The Terminal app echoes lines received on the USB/UART console onto the
  e-paper. (@wip: needs the Terminal app + EMU_EPD_REFRESH.)

  Scenario: A typed line is echoed and shown
    Given the badge firmware has booted
    When I type "@app terminal" on the serial console
    And I type "hello supercon" on the serial console
    Then I should see "hello supercon" within 10 seconds
    And at least 1 e-paper refresh has occurred
