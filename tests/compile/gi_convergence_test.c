#include "../../native/hlvrad/gi_convergence.h"
#include <assert.h>
int main(void) {
    assert(GIChange(0, 0) == 0);
    assert(GIChange(0, 0.001) < 1);
    assert(GIChange(0.1, 0.2) > 1); /* dark room still gaining light */
    assert(GIChange(10, 9) > 1); /* decreases must not stop the bake */
    assert(GIChange(100, 100.01) < 1);
    assert(GIChange(100.01, 100) < 1);
    assert(GIChange(NAN, 0) > 1);
    assert(GIChange(0, INFINITY) > 1);
    return 0;
}
