@emu @wip @slow
Feature: On-device TinyStories LLM
  The TinyStories app runs llama2.c (stories260K + tok512 from /data/models) and
  streams generated tokens. Tagged wip (needs the LLM app wired to a launch
  trigger) and slow (generation on the emulated CPU takes a while).

  Scenario: Launching TinyStories generates text
    Given the badge firmware has booted
    When I type "@app tinystories" on the serial console
    Then I should see "tok/s" within 180 seconds
    And at least 1 e-paper refresh has occurred
