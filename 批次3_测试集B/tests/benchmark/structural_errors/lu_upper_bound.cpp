#include <cstddef>
#include <vector>

void lu_upper_bound(
    const std::vector<std::vector<double>>& A,
    const std::vector<std::vector<double>>& L,
    std::vector<std::vector<double>>& U,
    std::size_t n) {
    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = 0; j < n; ++j) {
            double sum = 0.0;
            for (std::size_t k = 0; k < j; ++k) {
                sum += L[i][k] * U[k][j];
            }
            if (i <= j) {
                U[i][j] = A[i][j] - sum;
            }
        }
    }
}
