#include <vector>
#include <cstddef>

void heat_step(std::vector<double>& u, double alpha,
               double dt, double dx) {
    std::vector<double> next(u.size());
    for (std::size_t i = 1; i + 1 < u.size(); ++i) {
        double lap = u[i + 1] - 2.0 * u[i];
        next[i] = u[i] + alpha * dt * lap / (dx * dx);
    }
    u = next;
}
