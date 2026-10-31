/* Each check returns 1 when the C entry sees rsp = 8 mod 16, else 0.
 * align6 has no stack arguments; align7 has one (odd, needs padding);
 * align8 has two (even, no padding). */
    .text
    .macro align_check name
    .globl \name
    .type \name,@function
\name:
    mov %rsp, %rax
    and $15, %rax
    cmp $8, %rax
    sete %al
    movzbl %al, %eax
    ret
    .endm
    align_check align6
    align_check align7
    align_check align8
    .section .note.GNU-stack,"",@progbits
