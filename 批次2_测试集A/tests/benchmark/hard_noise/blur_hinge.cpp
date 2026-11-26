#include <vector>
#include <cstddef>

double hinge_loss(const std::vector<double>& y,
                  const std::vector<double>& wx) {
    double total = 0.0;
    for (std::size_t i = 0; i < y.size(); ++i) {
        total += 1.0 - y[i] * wx[i];
    }
    return total / static_cast<double>(y.size());
}
