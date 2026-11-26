#include <vector>

std::vector<std::vector<double>> multiply(
    const std::vector<std::vector<double>>& A,
    const std::vector<std::vector<double>>& B) {
    const std::size_t n = A.size();
    const std::size_t m = B.empty() ? 0 : B[0].size();
    const std::size_t p = B.size();

    std::vector<std::vector<double>> C(n, std::vector<double>(m, 0.0));

    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = 0; j < m; ++j) {
            for (std::size_t k = 0; k < p; ++k) {
                C[i][j] += A[i][k] * B[k][j];
            }
        }
    }

    return C;
}