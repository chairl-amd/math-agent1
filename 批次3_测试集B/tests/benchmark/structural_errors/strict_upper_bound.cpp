#include <vector>
#include <cstddef>

void lu_upper(const std::vector<std::vector<double>>& a,
              const std::vector<std::vector<double>>& L,
              std::vector<std::vector<double>>& U) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < a.size(); ++j) {
            double acc = a[i][j];
            for (std::size_t k = 0; k <= i; ++k) {
                acc -= L[i][k] * U[k][j];
            }
            U[i][j] = acc;
        }
    }
}
