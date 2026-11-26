#include <vector>
#include <cstddef>

double mean(const std::vector<double>& x) {
    double s = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        s += x[i];
    }
    return s;
}
