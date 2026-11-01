/* Fake foreign handles: malloc'd objects, a release count and a trace hook so a test can prove the
 * C call did not happen on a use-after-close or a dropped handle. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "foreign_fixture.h"

struct Fk { int v; };
struct FkBad { int v; };

static int ff_frees_count;

static void ff_trace(const char *what) {
    const char *t = getenv("FF_TRACE");
    if (t != NULL && t[0] == '1') {
        ssize_t w = write(2, what, strlen(what));
        (void)w;
    }
}

Fk *ff_new(int v) {
    Fk *p = (Fk *)malloc(sizeof *p);
    if (p == NULL) return NULL;
    p->v = v;
    ff_trace("ff_new\n");
    return p;
}

Fk *ff_new_opt(int v) {
    if (v < 0) return NULL;
    return ff_new(v);
}

int ff_get(const Fk *p) { return p->v; }

/* A borrow that traces its own entry, so use-after-close can assert it was never called. */
int ff_mark(const Fk *p) {
    ff_trace("ff_mark\n");
    return p->v;
}

int ff_touch(Fk *p) {
    p->v += 1;
    return p->v;
}

void ff_release(Fk *p) {
    ff_trace("ff_release\n");
    ff_frees_count++;
    free(p);
}

int ff_frees(void) { return ff_frees_count; }

FkBad *ff_bad_new(void) {
    FkBad *p = (FkBad *)malloc(sizeof *p);
    if (p == NULL) return NULL;
    p->v = 1;
    return p;
}

/* A release_result "int" that reports an error: close must return err("foreign release failed", 42)
 * and the handle must be closed (word 0) anyway. */
int ff_bad_release(FkBad *p) {
    int v = p->v;
    free(p);
    return v == 1 ? 42 : 0;
}
