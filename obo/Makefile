# obo: the ownership language. Its compiler is written in obo (src/obo.obo) and is
# built by seed/obo, the last promoted compiler. Nothing here needs obx.
CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

# .PHONY matters for tools: obo/tools/ is an existing directory, so a non-phony
# target of that name would be "up to date" whenever the directory is not older
# than its prerequisites, and the tool and library sources are deliberately not
# prerequisites (the builder checks them).
#
# Stage 2 symbol files live in .obo/sym, one file per module, a flat namespace. The
# directory is an order-only prerequisite: creating/touching it must not rebuild the
# tools. The builder creates it too, so a direct `python3 tools/build_tools.py` works.
SYMDIR = .obo/sym
.PHONY: tools tools-check tools-memory all clean $(SYMDIR)

all: runtime_text.bin runtime_data.bin obo

# the compiler, built from source by the seed compiler
obo: src/obo.obo seed/obo runtime_text.bin runtime_data.bin
	./seed/obo src/obo.obo -o obo --rt .

runtime.elf: runtime/runtime.c runtime/entry.S runtime/rt.ld
	gcc $(CFLAGS) -c runtime/runtime.c -o runtime.o
	gcc -c runtime/entry.S -o entry.o
	ld -T runtime/rt.ld -o runtime.elf entry.o runtime.o

runtime_text.bin: runtime.elf
	objcopy -O binary -j .text runtime.elf $@

runtime_data.bin: runtime.elf
	objcopy -O binary -j .data --set-section-flags .data=alloc,load,contents runtime.elf $@

# the same compiler with register allocation off (REG_HOMES = 0), to compare code and speed with it on
obo-noregs: src/obo.obo seed/obo runtime_text.bin runtime_data.bin
	sed 's/^const REG_HOMES = .*/const REG_HOMES = 0/' src/obo.obo > .noregs.obo
	./seed/obo .noregs.obo -o obo-noregs --rt .
	rm -f .noregs.obo

# the runtime's own I/O contract harness (tests/io_test.py builds and runs it in a temp dir too)
rt_io_test: tests/rt_io_test.c runtime/runtime.c runtime/rt.ld
	gcc $(CFLAGS) -c tests/rt_io_test.c -o rt_io_test.o
	ld -T runtime/rt.ld -o rt_io_test rt_io_test.o

# the first command-line tools: lib/str.obo + lib/io.obo + one programs/*.obx each,
# concatenated and compiled by tools/build_tools.py (the sources are not Make
# prerequisites; .PHONY makes every source edit rebuild, and the builder's own
# checks report a missing phase 2 instead of make reporting a missing target)
tools: obo runtime_text.bin runtime_data.bin | $(SYMDIR)
	python3 tools/build_tools.py

$(SYMDIR):
	mkdir -p $(SYMDIR)

# the differential gate against GNU coreutils (separate from make promote)
tools-check: tools
	python3 tests/tools.py

tools-memory: tools
	python3 tests/tools.py --memory

# make the current compiler the new seed, once it is a fixed point and the suite passes
promote: obo
	./obo src/obo.obo -o .stage2 --rt .
	./.stage2 src/obo.obo -o .stage3 --rt .
	cmp .stage2 .stage3
	OBO=./.stage2 python3 tests/run_fork.py
	cp .stage2 seed/obo
	rm -f .stage2 .stage3
	@echo "seed/obo updated"

clean:
	rm -rf build
	rm -f *.o *.elf *.bin obo obo-noregs .noregs.obo .stage2 .stage3 rt_io_test
	rm -rf .obo
