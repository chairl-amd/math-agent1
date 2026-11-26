#include <cmath>
#include <vector>

double l2_norm(const std::vector<double>& x) {
    double acc = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        acc += x[i] * x[i];
    }
    return std::sqrt(acc);
}
