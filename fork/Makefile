# obo: the ownership fork. Its compiler is written in mainline obx (with GC) and
# built by ../obxc; it is not self-hosting until the ownership model is proven.
CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

all: runtime_text.bin runtime_data.bin obo

obo: obo.obx ../obxc
	../obxc obo.obx -o obo --rt ..

runtime.elf: runtime/runtime.c runtime/entry.S runtime/rt.ld
	gcc $(CFLAGS) -c runtime/runtime.c -o runtime.o
	gcc -c runtime/entry.S -o entry.o
	ld -T runtime/rt.ld -o runtime.elf entry.o runtime.o

runtime_text.bin: runtime.elf
	objcopy -O binary -j .text runtime.elf $@

runtime_data.bin: runtime.elf
	objcopy -O binary -j .data --set-section-flags .data=alloc,load,contents runtime.elf $@

clean:
	rm -f *.o *.elf *.bin obo
