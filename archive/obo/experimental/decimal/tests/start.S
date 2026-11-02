/* private freestanding test entry; no libc */
    .section .text
    .globl _start
_start:
    call dec24_all_test_main
    mov %eax, %edi
    mov $60, %eax
    syscall
    hlt
    .section .note.GNU-stack,"",@progbits
