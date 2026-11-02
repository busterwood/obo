/* The native runtime's fixed layout (design/native.md, "ELF layout"): its code and read-only
   data at 0x401000, right after the ELF headers' page; its data and bss at 0x40000000. The
   generated program's code follows the runtime's code; its data follows the runtime's bss. */
ENTRY(rt_n_start)
SECTIONS {
  . = 0x401000;
  .text : { *(.text*) *(.rodata*) }
  . = 0x40000000;
  .data : { *(.data*) }
  .bss : { *(.bss*) *(COMMON) }
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
