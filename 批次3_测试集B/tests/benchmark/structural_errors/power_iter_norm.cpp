#include <vector>
#include <cmath>
#include <cstddef>

void power_iter(const std::vector<std::vector<double>>& A,
                std::vector<double>& v) {
    std::vector<double> w(v.size(), 0.0);
    for (std::size_t i = 0; i < v.size(); ++i) {
        for (std::size_t j = 0; j < v.size(); ++j) {
            w[i] += A[i][j] * v[j];
        }
    }
    double norm = 0.0;
    for (std::size_t i = 0; i < w.size(); ++i) {
        norm += w[i];
    }
    for (std::size_t i = 0; i < v.size(); ++i) {
        v[i] = w[i] / norm;
    }
}
