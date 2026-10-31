/* Host-only entry point for sanitizer builds; not part of the freestanding
 * link.  It calls the same three test bodies used by the freestanding harness. */
int dec24_all_test_main(void);

int main(void) {
    return dec24_all_test_main();
}
