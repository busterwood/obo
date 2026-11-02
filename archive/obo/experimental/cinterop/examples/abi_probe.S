/* Freestanding ABI probe: calls generated thunks with the obo stack convention and
 * checks the result plus rbx/rbp/r12-r15 preservation. r8-r11 sentinels are saved
 * on the stack to mimic the ordinary obo caller's protection, since C may clobber
 * them. No libc and no dynamic loader are involved. */
    .text
    .globl _start
    .macro set_sents
    movabs $0x1111111111111111, %rbx
    movabs $0x2222222222222222, %rbp
    movabs $0x3333333333333333, %r12
    movabs $0x4444444444444444, %r13
    movabs $0x5555555555555555, %r14
    movabs $0x6666666666666666, %r15
    movabs $0x8181818181818181, %r8
    movabs $0x8282828282828282, %r9
    movabs $0x8383838383838383, %r10
    movabs $0x8484848484848484, %r11
    .endm
    .macro save_r8
    push %r8
    push %r9
    push %r10
    push %r11
    .endm
    .macro check_callee
    movabs $0x1111111111111111, %rax
    cmp %rax, %rbx
    jne fail
    movabs $0x2222222222222222, %rax
    cmp %rax, %rbp
    jne fail
    movabs $0x3333333333333333, %rax
    cmp %rax, %r12
    jne fail
    movabs $0x4444444444444444, %rax
    cmp %rax, %r13
    jne fail
    movabs $0x5555555555555555, %rax
    cmp %rax, %r14
    jne fail
    movabs $0x6666666666666666, %rax
    cmp %rax, %r15
    jne fail
    .endm
    .macro check_r8
    pop %r11
    movabs $0x8484848484848484, %rax
    cmp %rax, %r11
    jne fail
    pop %r10
    movabs $0x8383838383838383, %rax
    cmp %rax, %r10
    jne fail
    pop %r9
    movabs $0x8282828282828282, %rax
    cmp %rax, %r9
    jne fail
    pop %r8
    movabs $0x8181818181818181, %rax
    cmp %rax, %r8
    jne fail
    .endm
_start:
    set_sents
    save_r8
    push $20
    push $22
    call obo_native_add
    add $16, %rsp
    cmp $42, %rax
    jne fail
    check_callee
    check_r8

    set_sents
    save_r8
    sub $8, %rsp                 /* odd 7 arguments: pad before them */
    push $7
    push $6
    push $5
    push $4
    push $3
    push $2
    push $1
    call obo_native_sum7
    add $64, %rsp
    cmp $28, %rax
    jne fail
    check_callee
    check_r8

    set_sents
    save_r8
    push $8
    push $7
    push $6
    push $5
    push $4
    push $3
    push $2
    push $1
    call obo_native_sum8
    add $64, %rsp
    cmp $36, %rax
    jne fail
    check_callee
    check_r8

    xor %edi, %edi
    mov $60, %eax
    syscall

fail:
    mov $1, %edi
    mov $60, %eax
    syscall
    .section .note.GNU-stack,"",@progbits
