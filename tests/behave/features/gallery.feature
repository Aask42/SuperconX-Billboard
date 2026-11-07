@emu
Feature: Gallery app
  The Gallery app (issue #6) decodes a PNG from /data/gallery (001..044) straight
  into the colour planes via lodepng + badge_core's blit, centered on the panel.

  @fbdump
  Scenario: Selecting a Gallery image decodes and paints it
    Given the badge firmware has booted
    When I type "@app gallery" on the serial console
    When I type "@gallery 2" on the serial console
    # Stream order: the selection is applied, THEN image 002 is rendered — so the
    # plane dump reflects the selected image, not whatever launched by default.
    Then I should see "gallery index set: 2" within 10 seconds
    And "gallery rendered: /data/gallery/002.png" appears within 10 seconds
    And at least 2 e-paper refreshes have occurred
    And the panel has black ink in rows 0 to 672
    And the panel has red ink in rows 0 to 672
    And the e-paper screen is saved to "gallery.png"
