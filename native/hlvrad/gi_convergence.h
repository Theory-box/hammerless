/* Lightmap RGB units: tolerate 0.01 absolute plus 0.2% of bounced light.
 * Use the larger magnitude so both growing and decreasing solutions are checked. */
#include <math.h>
static double GIChange(double previous, double next) {
    if (!isfinite(previous) || !isfinite(next)) return HUGE_VAL;
    return fabs(next - previous) / (0.01 + 0.002 * fmax(fabs(previous), fabs(next)));
}
