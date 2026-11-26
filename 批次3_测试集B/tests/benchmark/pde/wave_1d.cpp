#include <vector>
#include <cstddef>

void wave_step(std::vector<double>& u, std::vector<double>& u_prev,
               double c, double dt, double dx) {
    std::vector<double> next = u;
    double r = (c * dt / dx) * (c * dt / dx);
    for (std::size_t i = 1; i + 1 < u.size(); ++i) {
        double lap = u[i + 1] - 2.0 * u[i] + u[i - 1];
        next[i] = 2.0 * u[i] - u_prev[i] + r * lap;
    }
    u_prev = u;
    u = next;
}
