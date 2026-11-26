#include <vector>
#include <cstddef>

void matmul(const std::vector<std::vector<double>>& a,
            const std::vector<std::vector<double>>& b,
            std::vector<std::vector<double>>& c) {
    for (std::size_t i = 0; i < a.size(); ++i) {
        for (std::size_t j = 0; j < b.size(); ++j) {
            double acc = 0.0;
            for (std::size_t k = 0; k < b.size(); ++k) {
                acc += a[k][j] * b[k][i];
            }
            c[i][j] = acc;
        }
    }
}
