/* schedule.csv parser (Schedule / Workshops apps). Pure C, no allocation.
 * Mirrors the format the badge ships; see badge_core.h. */
#include "badge_core.h"

#include <string.h>

static int valid_time(const char *s)
{
    size_t n = strlen(s);
    if (n < 4 || n > 5) return 0;
    size_t colon = (n == 5) ? 2 : 1;
    for (size_t i = 0; i < n; i++) {
        if (i == colon) { if (s[i] != ':') return 0; }
        else if (s[i] < '0' || s[i] > '9') return 0;
    }
    return 1;
}

/* Copy a field into dst[cap], unescaping "" -> " and NUL-terminating (truncates). */
static void copy_field(char *dst, int cap, const char *p, int len)
{
    int o = 0;
    for (int i = 0; i < len && o < cap - 1; i++) {
        if (p[i] == '"' && i + 1 < len && p[i + 1] == '"') { dst[o++] = '"'; i++; }
        else dst[o++] = p[i];
    }
    dst[o] = 0;
}

/* Split one record into fields honouring double-quotes. Stores up to maxf field
 * spans; returns the TOTAL field count (so callers can reject rows with != 6). */
static int split_line(const char *line, int linelen, const char **starts, int *lens, int maxf)
{
    int nf = 0, i = 0;
    for (;;) {
        const char *fstart;
        int flen;
        if (i < linelen && line[i] == '"') {
            i++;
            fstart = line + i;
            while (i < linelen) {
                if (line[i] == '"') {
                    if (i + 1 < linelen && line[i + 1] == '"') { i += 2; continue; }
                    break;
                }
                i++;
            }
            flen = (int)(line + i - fstart);
            if (i < linelen && line[i] == '"') i++;   /* closing quote */
        } else {
            fstart = line + i;
            while (i < linelen && line[i] != ',') i++;
            flen = (int)(line + i - fstart);
        }
        if (nf < maxf) { starts[nf] = fstart; lens[nf] = flen; }
        nf++;
        if (i < linelen && line[i] == ',') { i++; continue; }
        break;
    }
    return nf;
}

int badge_schedule_parse(const char *csv, badge_talk_t *out, int max)
{
    if (!csv || !out || max <= 0) return 0;
    int count = 0;
    const char *p = csv;
    int header = 1;

    while (*p && count < max) {
        const char *ls = p;
        while (*p && *p != '\n') p++;
        int linelen = (int)(p - ls);
        if (linelen > 0 && ls[linelen - 1] == '\r') linelen--;   /* CRLF */
        if (*p == '\n') p++;

        if (header) { header = 0; continue; }
        if (linelen == 0) continue;                              /* blank line */

        const char *st[6];
        int ln[6];
        if (split_line(ls, linelen, st, ln, 6) != 6) continue;   /* malformed */

        char timebuf[8];
        copy_field(timebuf, sizeof timebuf, st[1], ln[1]);
        if (!valid_time(timebuf)) continue;

        badge_talk_t *t = &out[count];
        copy_field(t->day, sizeof t->day, st[0], ln[0]);
        memcpy(t->time, timebuf, sizeof timebuf);
        copy_field(t->stage, sizeof t->stage, st[2], ln[2]);
        copy_field(t->talk, sizeof t->talk, st[3], ln[3]);
        copy_field(t->speaker, sizeof t->speaker, st[4], ln[4]);
        copy_field(t->image, sizeof t->image, st[5], ln[5]);
        count++;
    }
    return count;
}

int badge_schedule_is_talk(const char *stage)
{
    return strcmp(stage, "Lamarr") == 0 || strcmp(stage, "Hopper") == 0;
}
