# obo: the ownership language. Its compiler is written in obo (src/obo.obo) and is
# built by seed/obo, the last promoted compiler. Nothing here needs obx.
CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

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
	rm -f *.o *.elf *.bin obo obo-noregs .noregs.obo .stage2 .stage3 rt_io_test
