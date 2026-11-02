/* Fake P2 backend entry points used by sysadapter_fake.c. */
#ifndef OBO_SYS_NET_FAKE_H
#define OBO_SYS_NET_FAKE_H

#include <stddef.h>

struct fake_file;
struct fake_sock;

long fake_sock_read(struct fake_file *f, unsigned char *buf, size_t offset,
                    size_t count);
long fake_sock_write(struct fake_file *f, const unsigned char *buf,
                     size_t count);
void fake_sock_release(struct fake_sock *s);
int fake_pop_script(long call_class, long *result);
long fake_clock_now(void);
void fake_clock_set(long value);

long fake_reactor_new(long max_watches, long max_timers);
long fake_cancel_new(void);
void fake_net_forget_source(long token);

#endif
