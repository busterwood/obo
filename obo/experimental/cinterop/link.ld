/* Experimental non-PIE layout: keep the compiler's fixed obo addresses and put
 * the host CRT, PLT and dynamic tables above them. This script is a prototype
 * bridge, not a production linker layout. */
ENTRY(_start)
SECTIONS
{
  . = 0x401000;
  .obo.text : { *(.obo.text) }
  . = 0x08000000;
  .obo.rodata : { *(.obo.rodata) }
  . = 0x0c000000;
  .obo.bss (NOLOAD) : { *(.obo.bss) }
  . = 0x10000000;
  .obo.rttext : { *(.obo.rttext) }
  . = 0x20000000;
  .obo.rtdata : { *(.obo.rtdata) }

  . = 0x03000000;
  .plt : { *(.plt) *(.plt.*) *(.iplt) }
  .text : { *(.text) *(.text.*) }
  . = ALIGN(0x1000);
  .rodata : { *(.rodata) *(.rodata.*) }
  . = ALIGN(0x1000);
  .data : { *(.data) *(.data.*) *(.got) *(.got.*) }
  .bss : { *(.bss) *(.bss.*) *(COMMON) }
  . = ALIGN(0x1000);
  .dynamic : { *(.dynamic) }
  .interp : { *(.interp) }
  .dynsym : { *(.dynsym) }
  .dynstr : { *(.dynstr) }
  .hash : { *(.hash) }
  .gnu.hash : { *(.gnu.hash) }
  .gnu.version : { *(.gnu.version) }
  .gnu.version_r : { *(.gnu.version_r) }
  .rela.dyn : { *(.rela.dyn) }
  .rela.plt : { *(.rela.plt) }
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
