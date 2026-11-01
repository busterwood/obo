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
  .init : { *(.init) }
  .fini : { *(.fini) }
  . = ALIGN(0x1000);
  /* Host .text has a fixed 128 KiB slot (0x03000000..0x03020000). The bound is
   * a checked ceiling, not a measured budget: the link fails if host code
   * outgrows it. The current examples use only a few KiB here (readelf -S shows
   * .text); raise the bound only with a measured reason. */
  ASSERT(. <= 0x03020000, "host .text exceeds its 128 KiB slot")
  .rodata : { *(.rodata) *(.rodata.*) }
  . = ALIGN(0x1000);
  /* Full RELRO: the dynamic linker makes these read-only after relocation.
     They must be separate output sections for ld to emit PT_GNU_RELRO; a
     single .data that also absorbs .got defeats -z relro. */
  . = DATA_SEGMENT_ALIGN (CONSTANT (MAXPAGESIZE), CONSTANT (COMMONPAGESIZE));
  /* Constructors/destructors are relocated before the loader makes the RELRO
     range read-only, so keep them inside it. */
  .preinit_array : { *(.preinit_array) }
  .init_array : { *(.init_array) *(.ctors) }
  .fini_array : { *(.fini_array) *(.dtors) }
  .data.rel.ro : { *(.data.rel.ro) *(.data.rel.ro.*) }
  /* .dynamic belongs in the RELRO range too; the loader writes DT_DEBUG before
     it removes write permission. */
  .dynamic : { *(.dynamic) }
  .got : { *(.got) *(.got.*) }
  /* A custom SECTIONS script does not get the default script's RELRO end
     marker automatically: without DATA_SEGMENT_ALIGN/RELRO_END ld drops
     PT_GNU_RELRO even with -z relro. The offset keeps the first 24 bytes of
     .got.plt in the protected range, as the default script does. */
  . = DATA_SEGMENT_RELRO_END (SIZEOF (.got.plt) >= 24 ? 24 : 0, .);
  .got.plt : { *(.got.plt) }
  .data : { *(.data) *(.data.*) }
  .bss : { *(.bss) *(.bss.*) *(COMMON) }
  . = ALIGN(0x1000);
  /* Static host data (C runtime and adapter tables, host bss). The obo entry
     stack is not in this layout: host_stack.c maps it with mmap as a guarded
     stack (PROT_NONE pages below and above), so it does not consume this slot. */
  ASSERT(. <= 0x03180000, "host .rodata/.data/.bss overlap the dynamic tables")
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
  . = DATA_SEGMENT_END (.);
  /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
