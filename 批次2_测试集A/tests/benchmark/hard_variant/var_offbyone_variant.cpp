#include <vector>
#include <cstddef>

double sum_skip_first(const std::vector<double>& a) {
    double s = 0.0;
    for (std::size_t i = 1; i < a.size(); ++i) {
        s += a[i];
    }
    return s;
}
