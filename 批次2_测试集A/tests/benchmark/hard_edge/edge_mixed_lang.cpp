#include <vector>
#include <cstddef>

void step(std::vector<double>& w, const std::vector<double>& grad,
          double eta) {
    for (std::size_t i = 0; i < w.size(); ++i) {
        w[i] += eta * grad[i];
    }
}
