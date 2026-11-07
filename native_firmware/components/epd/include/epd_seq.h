/*
 * epd_seq.h — the e-paper update *sequence*, isolated behind a tiny HAL so it can
 * run on the target (real GPIO/SPI) AND on the host (a capture HAL that records
 * the exact command stream for the golden-sequence test, issue #4).
 *
 * The sequence here is the port of epd.py's global_update pipeline. Keeping it
 * free of ESP-IDF lets tests/unit/test_epd_sequence.py compile it and assert the
 * driver emits the stock command order / data / delays, and leaves CS, RST and
 * the power rail in documented final states.
 */
#ifndef EPD_SEQ_H
#define EPD_SEQ_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    EPD_T_BOTH  = 0,   /* both CoGs (CS1+CS2) */
    EPD_T_LEFT  = 1,   /* left half  (CS1)    */
    EPD_T_RIGHT = 2,   /* right half (CS2)    */
} epd_target_t;

/* Low-level operations the sequence performs. The target build wires these to
 * GPIO/SPI; the host test wires them to a recorder. */
typedef struct {
    void (*cmd)(epd_target_t target, uint8_t index, const uint8_t *data, size_t len);
    void (*dc)(int level);        /* raw D/C pin (used by the shutdown tail) */
    void (*cs)(int line, int level);  /* raw CS pin: line 1 or 2 */
    void (*rst)(int level);       /* raw reset pin */
    void (*pwr)(int on);          /* panel power-enable (GPIO48) */
    void (*delay_ms)(unsigned ms);
    void (*wait_busy)(void);      /* block until both BUSY lines are high */
} epd_seq_hal_t;

void epd_seq_set_hal(const epd_seq_hal_t *hal);

/* Run one full global update from four pre-split half-planes (each half_bytes
 * long, MONO_HLSB). reverse_bits mirrors epd.py's per-byte bit reversal. */
void epd_seq_refresh(const uint8_t *black_left, const uint8_t *red_left,
                     const uint8_t *black_right, const uint8_t *red_right,
                     size_t half_bytes, int reverse_bits);

#ifdef __cplusplus
}
#endif

#endif /* EPD_SEQ_H */
