/* Internal shared registry for the experimental systems adapter.
 *
 * This is intentionally separate from experimental/cinterop/toolkit/cres.* so
 * that P0 does not collide with the database-adapter work in another worktree.
 * It is trusted C support code, not an application-facing API.
 */
#ifndef OBO_SYS_REGISTRY_H
#define OBO_SYS_REGISTRY_H

#include "sysadapter.h"

/* Register an object. `release` returns 0 on success or a positive errno after
 * consuming/retiring the underlying native resource. */
long sys_reg_new_object(int kind, void *ptr, int (*release)(void *), size_t bytes);

/* Register a C-owned byte buffer of `len` bytes (0..SYS_MAX_BYTE_BUFFER). */
long sys_reg_new_buffer(long len);

/* Live pointer for a token of the expected kind, or NULL. */
void *sys_reg_peek(long token, int kind);

/* Retire a token exactly once, returning its pointer/release through out
 * parameters. Returns 1 if this call retired it, 0 if stale/wrong-kind. */
long sys_reg_retire(long token, int kind, void **ptr, int (**release)(void *));

/* Shared byte-buffer operations. Returns >= 0 result or -errno. */
long sys_reg_buffer_op(void *ptr, long op, long a, long b, long c);

long sys_reg_live_count(void);
long sys_reg_live_kind(long kind);
long sys_reg_bytes(void);
long sys_reg_cleanup_failures(void);
void sys_reg_add_cleanup_failure(void);
long sys_reg_call_count(long call_class);
void sys_reg_bump_call(long call_class);
void sys_reg_reset_counts(void);

#endif
