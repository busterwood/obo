CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

all: runtime_text.bin runtime_data.bin obxc

# the compiler, built from source by the bootstrap compiler
obxc: selfhost/obxc.obx bootstrap/obxc runtime_text.bin runtime_data.bin
	./bootstrap/obxc selfhost/obxc.obx -o obxc --rt .

runtime.elf: runtime.c entry.S rt.ld
	gcc $(CFLAGS) -c runtime.c -o runtime.o
	gcc -c entry.S -o entry.o
	ld -T rt.ld -o runtime.elf entry.o runtime.o

runtime_text.bin: runtime.elf
	objcopy -O binary -j .text runtime.elf $@

runtime_data.bin: runtime.elf
	objcopy -O binary -j .data --set-section-flags .data=alloc,load,contents runtime.elf $@

# make the current compiler the new bootstrap, once it is a fixed point and the suite passes
promote: obxc
	./obxc selfhost/obxc.obx -o .stage2 --rt .
	./.stage2 selfhost/obxc.obx -o .stage3 --rt .
	cmp .stage2 .stage3
	OBXC=./.stage2 python3 tests/run.py -x stage/
	cp .stage2 bootstrap/obxc
	rm -f .stage2 .stage3
	@echo "bootstrap/obxc updated"

clean:
	rm -f *.o *.elf *.bin obxc .stage2 .stage3
