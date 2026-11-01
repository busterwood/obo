/* Fake foreign-handle fixture for the stage (d) tests. */
#ifndef CINTEROP_FOREIGN_FIXTURE_H
#define CINTEROP_FOREIGN_FIXTURE_H

typedef struct Fk Fk;
typedef struct FkBad FkBad;

Fk *ff_new(int v);
Fk *ff_new_opt(int v);
int ff_get(Fk *p);
int ff_mark(Fk *p);
void ff_release(Fk *p);
int ff_frees(void);

FkBad *ff_bad_new(void);
int ff_bad_release(FkBad *p);

#endif
