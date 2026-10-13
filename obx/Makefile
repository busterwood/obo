CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

all: runtime_text.bin runtime_data.bin obxc

# the compiler, built from source by the seed compiler
obxc: src/obxc.obx seed/obxc runtime_text.bin runtime_data.bin
	./seed/obxc src/obxc.obx -o obxc --rt .

runtime.elf: runtime/runtime.c runtime/entry.S runtime/rt.ld
	gcc $(CFLAGS) -c runtime/runtime.c -o runtime.o
	gcc -c runtime/entry.S -o entry.o
	ld -T runtime/rt.ld -o runtime.elf entry.o runtime.o

runtime_text.bin: runtime.elf
	objcopy -O binary -j .text runtime.elf $@

runtime_data.bin: runtime.elf
	objcopy -O binary -j .data --set-section-flags .data=alloc,load,contents runtime.elf $@

# make the current compiler the new seed, once it is a fixed point and the suite passes
promote: obxc
	./obxc src/obxc.obx -o .stage2 --rt .
	./.stage2 src/obxc.obx -o .stage3 --rt .
	cmp .stage2 .stage3
	OBXC=./.stage2 python3 tests/run.py -x stage/
	cp .stage2 seed/obxc
	rm -f .stage2 .stage3
	@echo "seed/obxc updated"

clean:
	rm -f *.o *.elf *.bin obxc .stage2 .stage3
