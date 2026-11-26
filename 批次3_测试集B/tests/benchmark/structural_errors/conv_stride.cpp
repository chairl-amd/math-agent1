#include <vector>
#include <cstddef>

std::vector<double> conv(const std::vector<double>& x,
                         const std::vector<double>& w) {
    std::vector<double> y;
    for (std::size_t i = 0; i + 2 < x.size(); i += 2) {
        double acc = 0.0;
        for (std::size_t k = 0; k < w.size(); ++k) {
            acc += w[k] * x[i + k];
        }
        y.push_back(acc);
    }
    return y;
}
