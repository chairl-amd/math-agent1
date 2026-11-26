#include <vector>
#include <cstddef>

void ns_momentum(std::vector<double>& ux, std::vector<double>& uy,
                 const std::vector<double>& p,
                 double rho, double nu, double dt, double dx) {
    std::vector<double> next_x = ux;
    std::vector<double> next_y = uy;
    for (std::size_t i = 1; i + 1 < ux.size(); ++i) {
        double adv = ux[i] * (ux[i + 1] - ux[i - 1]) / (2.0 * dx);
        double diff = nu * (ux[i + 1] - 2.0 * ux[i] + ux[i - 1]) / (dx * dx);
        double grad_p = (p[i + 1] - p[i - 1]) / (2.0 * dx);
        next_x[i] = ux[i] + dt * (-adv - grad_p / rho + diff);
        next_y[i] = uy[i];
    }
    ux = next_x;
    uy = next_y;
}
