/* Intentional compiler errors for testing vigor quickfix diagnostics. */
#include <stdio.h>

static int add(int left, int right)
{
    return left + right;
}

int main(void)
{
    int total = add(1);                 /* too few arguments */
    printf("missing = %d\n", missing); /* undeclared identifier */
    return total + unknown_function(); /* undeclared function */
}
