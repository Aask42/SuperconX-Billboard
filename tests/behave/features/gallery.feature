@emu @wip
Feature: Gallery app
  The Gallery app shows a random image from /data/gallery (001..044) and re-rolls
  on a timer. Tagged wip: needs the app + T55 refresh logging.

  @fbdump
  Scenario: Launching the Gallery paints an image
    Given the badge firmware is running in the emulator
    When I type "@app gallery" on the serial console
    Then at least 1 e-paper refresh has occurred
    And the e-paper screen is saved to "gallery.png"
