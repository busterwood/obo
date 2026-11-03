/* The sieve of Eratosthenes as plain C, for bench/sieve.obx: the same work (ten rounds over a
   fresh array of n + 1 flags, marking from each prime's square), no checks. Built by bench.py
   with gcc -O2 as the "plain C" column. */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    long n = 10000000;
    long count = 0;
    for (int round = 0; round < 10; round++) {
        bool* prime = malloc(n + 1);
        if (!prime) return 1;
        memset(prime, true, n + 1);
        prime[0] = false;
        prime[1] = false;
        for (long i = 2; i * i <= n; i++) {
            if (prime[i]) {
                for (long j = i * i; j <= n; j += i) prime[j] = false;
            }
        }
        count = 0;
        for (long k = 0; k <= n; k++) {
            if (prime[k]) count++;
        }
        free(prime);
    }
    printf("%ld\n", count);
    return 0;
}
