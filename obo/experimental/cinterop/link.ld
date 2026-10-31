/* Experimental non-PIE layout: keep the compiler's fixed obo addresses and put
 * the host CRT, PLT and dynamic tables above them. The ASSERTs bound every
 * region; this is still a prototype bridge, not a production linker layout. */
ENTRY(_start)
SECTIONS
{
  . = 0x401000;
  .obo.text : { *(.obo.text) }
  ASSERT(. <= 0x08000000, ".obo.text overlaps the rodata region")

  . = 0x08000000;
  .obo.rodata : { *(.obo.rodata) }
  ASSERT(. <= 0x0c000000, ".obo.rodata overlaps the globals region")

  . = 0x0c000000;
  .obo.bss (NOLOAD) : { *(.obo.bss) }
  ASSERT(. <= 0x10000000, ".obo.bss overlaps the runtime text region")

  . = 0x10000000;
  .obo.rttext : { *(.obo.rttext) }
  ASSERT(. <= 0x20000000, ".obo.rttext overlaps the runtime data region")

  . = 0x20000000;
  .obo.rtdata : { *(.obo.rtdata) }
  ASSERT(. <= 0x30000000, ".obo.rtdata overlaps the host region")

  . = 0x03000000;
  .plt : { *(.plt) *(.plt.*) *(.iplt) }
  .text : { *(.text) *(.text.*) }
  . = ALIGN(0x1000);
  ASSERT(. <= 0x03010000, "host .text exceeds its 64 KiB slot")
  .rodata : { *(.rodata) *(.rodata.*) }
  . = ALIGN(0x1000);
  .data : { *(.data) *(.data.*) *(.got) *(.got.*) }
  .bss : { *(.bss) *(.bss.*) *(COMMON) }
  . = ALIGN(0x1000);
  ASSERT(. <= 0x03103000, "host .rodata/.data/.bss overlap the dynamic tables")
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
  ASSERT(. < 0x04000000, "host dynamic sections exceed their 16 MiB slot")
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
