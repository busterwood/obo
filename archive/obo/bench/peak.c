/* peak PROGRAM: runs PROGRAM and prints its peak resident memory in KB */
#include <stdio.h>
#include <unistd.h>
#include <sys/wait.h>
#include <sys/resource.h>
int main(int argc, char **argv) {
    pid_t p = fork();
    if (p == 0) { execv(argv[1], argv + 1); _exit(127); }
    int st; struct rusage ru;
    wait4(p, &st, 0, &ru);
    fprintf(stderr, "%ld KB peak, exit %d\n", ru.ru_maxrss, WEXITSTATUS(st));
    return 0;
}
