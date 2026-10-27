/* Fixed jump table: the compiler calls slot k at 0x10000000 + 8*k.
 * Each slot goes through a stub that checks the System V stack alignment
 * contract (rsp = 8 mod 16 on entry) before reaching the C function, so a
 * code generator alignment bug traps immediately instead of lurking until
 * some runtime routine happens to use aligned SSE stores. r11 is a scratch
 * register in the ABI and never carries an argument.
 *
 * The stub also records the stack pointer at entry. The collector scans only
 * from there upwards, i.e. only frames built by obx code, never the runtime's
 * own C frames: every root must already be on the obx stack when the runtime
 * is entered, and that contract is what the compiler guarantees. */
    .section .entry, "ax"
    .globl _entry
_entry:
    .macro slot f
    .balign 8
    jmp check_\f
    .endm
    slot rt_init
    slot rt_exit
    slot rt_alloc_record
    slot rt_alloc_array
    slot rt_print_int
    slot rt_print_str
    slot rt_print_bool
    slot rt_newline
    slot rt_concat
    slot rt_streq
    slot rt_panic_bounds
    slot rt_collect
    slot rt_gc_stats
    slot rt_int_to_str
    slot rt_append
    slot rt_truncate
    slot rt_bytes_to_string
    slot rt_substr
    slot rt_read_file
    slot rt_write_file
    slot rt_args
    slot rt_set_output
    slot rt_free
    slot rt_finish
    slot rt_str_inc
    slot rt_str_dec
    slot rt_handle
    slot rt_deref
    slot rt_alive
    slot rt_open_file
    slot rt_read_fd
    slot rt_write_fd
    slot rt_close_fd

    .text
    .macro check f
check_\f:
    mov %rsp, %r11
    and $15, %r11
    cmp $8, %r11
    jne rt_misaligned
    mov %rsp, rt_entry_sp      /* the collector scans the obx stack from here up */
    jmp \f
    .endm
    check rt_init
    check rt_exit
    check rt_alloc_record
    check rt_alloc_array
    check rt_print_int
    check rt_print_str
    check rt_print_bool
    check rt_newline
    check rt_concat
    check rt_streq
    check rt_panic_bounds
    check rt_collect
    check rt_gc_stats
    check rt_int_to_str
    check rt_append
    check rt_truncate
    check rt_bytes_to_string
    check rt_substr
    check rt_read_file
    check rt_write_file
    check rt_args
    check rt_set_output
    check rt_free
    check rt_finish
    check rt_str_inc
    check rt_str_dec
    check rt_handle
    check rt_deref
    check rt_alive
    check rt_open_file
    check rt_read_fd
    check rt_write_fd
    check rt_close_fd

    .section .note.GNU-stack,"",@progbits
