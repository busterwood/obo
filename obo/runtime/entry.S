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
    slot rt_read_dir_fd
    slot rt_stat_path
    slot rt_lstat_path
    slot rt_read_link
    slot rt_isatty
    slot rt_getenv
    slot rt_term_width
    slot rt_sleep
    slot rt_f_add
    slot rt_f_sub
    slot rt_f_mul
    slot rt_f_div
    slot rt_f_neg
    slot rt_f_eq
    slot rt_f_ne
    slot rt_f_lt
    slot rt_f_le
    slot rt_f_gt
    slot rt_f_ge
    slot rt_i_to_f
    slot rt_f_isnan
    slot rt_f_isinf
    slot rt_f_signbit
    slot rt_f_to_str
    slot rt_f_print
    slot rt_f_parse
#ifdef OBO_HOSTED_EXIT
    slot rt_set_host_exit
#else
    /* Slot 59 is reserved in the baseline build so decimal slots start at 60
     * in both the baseline and hosted tables; the hosted build uses 59 for its
     * exit callback. */
    slot rt_dec_reserved
#endif

    /* decimal integration (0.12): slots 60..72 append after the float block.
     * The hosted-exit slot stays 59, so both builds keep these indices. */
    slot rt_dec_add
    slot rt_dec_sub
    slot rt_dec_mul
    slot rt_dec_rem
    slot rt_dec_neg
    slot rt_dec_cmp
    slot rt_dec_print
    slot rt_dec_div_into
    slot rt_dec_quantize_into
    slot rt_dec_to_int
    slot rt_dec_from_int
    slot rt_dec_result
    slot rt_dec_int_result

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
    check rt_read_dir_fd
    check rt_stat_path
    check rt_lstat_path
    check rt_read_link
    check rt_isatty
    check rt_getenv
    check rt_term_width
    check rt_sleep
    check rt_f_add
    check rt_f_sub
    check rt_f_mul
    check rt_f_div
    check rt_f_neg
    check rt_f_eq
    check rt_f_ne
    check rt_f_lt
    check rt_f_le
    check rt_f_gt
    check rt_f_ge
    check rt_i_to_f
    check rt_f_isnan
    check rt_f_isinf
    check rt_f_signbit
    check rt_f_to_str
    check rt_f_print
    check rt_f_parse
#ifdef OBO_HOSTED_EXIT
    check rt_set_host_exit
#else
    check rt_dec_reserved
#endif
    check rt_dec_add
    check rt_dec_sub
    check rt_dec_mul
    check rt_dec_rem
    check rt_dec_neg
    check rt_dec_cmp
    check rt_dec_print
    check rt_dec_div_into
    check rt_dec_quantize_into
    check rt_dec_to_int
    check rt_dec_from_int
    check rt_dec_result
    check rt_dec_int_result

    .section .note.GNU-stack,"",@progbits
