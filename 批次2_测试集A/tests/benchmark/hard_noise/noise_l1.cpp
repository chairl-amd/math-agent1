#include <cmath>
#include <vector>
#include <cstddef>

double l1_norm(const std::vector<double>& x) {
    double s = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        s += std::fabs(x[i]);
    }
    return s;
}
