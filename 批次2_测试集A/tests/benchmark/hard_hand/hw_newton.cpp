#include <cmath>

double newton_step(double x, double fx, double dfx) {
    return x - fx / dfx;
}
