/* ASAN/UBSAN harness for the fake foreign fixture. It exercises the malloc/free paths the generated
 * release wrappers call; the obo-side record and census are checked by the OBO_CHECK=1 test app. */
#include <assert.h>
#include <stdio.h>

#include "foreign_fixture.h"

int main(void) {
    Fk *a = ff_new(3);
    assert(a != NULL);
    assert(ff_get(a) == 3);
    assert(ff_mark(a) == 3);
    assert(ff_touch(a) == 4);
    ff_release(a);

    assert(ff_new_opt(-1) == NULL);
    Fk *b = ff_new_opt(5);
    assert(b != NULL);
    assert(ff_get(b) == 5);
    ff_release(b);

    FkBad *d = ff_bad_new();
    assert(d != NULL);
    assert(ff_bad_release(d) == 42);

    assert(ff_frees() == 2);
    printf("foreign fixture tests ok\n");
    return 0;
}
