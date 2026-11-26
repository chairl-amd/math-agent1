#include <vector>
#include <cstddef>

void lu_factor(std::vector<std::vector<double>>& a,
               std::vector<std::vector<double>>& L,
               std::vector<std::vector<double>>& U) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < a.size(); ++j) {
            double acc = a[i][j];
            for (std::size_t k = 0; k < j; ++k) {
                acc -= L[i][k] * U[k][j];
            }
            if (j > i) {
                U[i][j] = acc;
            } else if (i == j) {
                L[i][i] = 1.0;
                U[i][i] = acc;
            } else {
                L[i][j] = acc / U[i][i];
            }
        }
    }
}
