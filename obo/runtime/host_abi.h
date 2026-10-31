/* Hosted-entry ABI for the experimental cinterop bridge.
 *
 * The baseline freestanding runtime is unchanged. The cinterop build compiles a
 * separate hosted runtime with -DOBO_HOSTED_EXIT; that variant appends one
 * jump-table slot after every existing slot. The compiler's RT_* constants and
 * the freestanding runtime blobs do not change.
 *
 * The cinterop build validates that entry.S still has exactly this many slots
 * before the hosted exit slot (59 existing slots -> index 59). If a future
 * runtime adds a slot, update this constant and the build check together.
 */
#ifndef OBO_RUNTIME_HOST_ABI_H
#define OBO_RUNTIME_HOST_ABI_H

#define RT_TEXT_BASE 0x10000000ULL
#define RT_HOST_EXIT_SLOT 59
#define RT_HOST_EXIT_ADDR (RT_TEXT_BASE + 8ULL * RT_HOST_EXIT_SLOT)

typedef void (*rt_host_exit_fn)(long code);

#endif
