/* all_test.c — aggregate the freestanding D1/D2 test bodies. */
int dec24_test_main(void);
int dec24_text_test_main(void);
int dec24_bind_test_main(void);
int dec24_arith_test_main(void);

int dec24_all_test_main(void) {
    int failures = 0;
    failures += dec24_test_main();
    failures += dec24_text_test_main();
    failures += dec24_bind_test_main();
    failures += dec24_arith_test_main();
    return failures > 255 ? 255 : failures;
}
