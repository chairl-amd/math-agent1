#include <vector>
#include <cstddef>

void poisson_sweep(std::vector<std::vector<double>>& phi,
                   const std::vector<std::vector<double>>& rho,
                   double eps0, double h) {
    for (std::size_t i = 1; i + 1 < phi.size(); ++i) {
        for (std::size_t j = 1; j + 1 < phi[i].size(); ++j) {
            double rhs = rho[i][j] / eps0;
            double lap = (phi[i + 1][j] + phi[i - 1][j]
                          + phi[i][j + 1] + phi[i][j - 1]
                          - 4.0 * phi[i][j]) / (h * h);
            phi[i][j] += 0.25 * (h * h * rhs - lap * h * h) + lap * h * h * 0.0;
        }
    }
}
