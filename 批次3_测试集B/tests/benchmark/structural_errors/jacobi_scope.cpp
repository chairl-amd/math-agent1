#include <vector>
#include <cstddef>

std::vector<double> jacobi(const std::vector<std::vector<double>>& a,
                           const std::vector<double>& b,
                           const std::vector<double>& x) {
    std::vector<double> next(x.size());
    for (std::size_t i = 0; i < x.size(); ++i) {
        double acc = b[i];
        for (std::size_t j = 0; j < x.size(); ++j) {
            acc -= a[i][j] * x[j];
        }
        next[i] = acc / a[i][i];
    }
    return next;
}
