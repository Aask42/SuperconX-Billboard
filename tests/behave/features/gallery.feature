@emu
Feature: Gallery app
  The Gallery app (issue #6) decodes a PNG from /data/gallery (001..044) straight
  into the colour planes via lodepng + badge_core's blit, centered on the panel.

  @fbdump
  Scenario: Launching the Gallery decodes and paints a real image
    Given the badge firmware has booted
    When I type "@app gallery" on the serial console
    When I type "@gallery 1" on the serial console
    Then I should see "gallery rendered" within 10 seconds
    And at least 2 e-paper refreshes have occurred
    And the panel has black ink in rows 0 to 672
    And the panel has red ink in rows 0 to 672
    And the e-paper screen is saved to "gallery.png"
