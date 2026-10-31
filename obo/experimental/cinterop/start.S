/* Experimental enter/launch shim. The host's normal crt1.o supplies _start and
 * calls main; host.c builds the initial-stack block and calls this to enter the
 * fixed obo image. This is still a prototype entry contract, not a stable ABI. */
    .text
    .globl _obo_launch
_obo_launch:
    mov %rdi, %rsp
    jmp _obo_start
    hlt
    .section .note.GNU-stack,"",@progbits
