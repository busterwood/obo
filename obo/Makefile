# obo: the ownership language. Its compiler is written in obx (with GC), in
# src/obo-on-obx.obx, and built by ../obx/obxc. It is not self-hosting until the
# ownership model is proven; src/obo.obo is the compiler written in obo.
CFLAGS = -O2 -ffreestanding -fno-builtin -nostdlib -fno-pic -fno-pie -fno-stack-protector \
         -fno-asynchronous-unwind-tables -mcmodel=small -fno-tree-loop-distribute-patterns -Wall

all: runtime_text.bin runtime_data.bin obo

obo: src/obo-on-obx.obx ../obx/obxc
	../obx/obxc src/obo-on-obx.obx -o obo --rt ../obx

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
