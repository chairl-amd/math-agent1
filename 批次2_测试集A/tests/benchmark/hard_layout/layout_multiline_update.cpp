#include <vector>
#include <cstddef>

void cg_step(const std::vector<std::vector<double>>& A,
             const std::vector<double>& b,
             std::vector<double>& x,
             double alpha) {
    std::vector<double> r(b.size(), 0.0);
    for (std::size_t i = 0; i < b.size(); ++i) {
        double ax = 0.0;
        for (std::size_t j = 0; j < b.size(); ++j) {
            ax += A[i][j] * x[j];
        }
        r[i] = b[i] - ax;
    }
    for (std::size_t i = 0; i < x.size(); ++i) {
        x[i] = x[i] + alpha * r[i];
    }
}
