ENTRY(_entry)
SECTIONS {
  . = 0x10000000;
  .text : { *(.entry) *(.text*) *(.rodata*) }
  . = 0x20000000;
  .data : { *(.data*) *(.bss*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
