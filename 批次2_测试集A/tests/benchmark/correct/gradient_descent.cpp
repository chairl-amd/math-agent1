#include <vector>

void gd_step(std::vector<double>& w,
             const std::vector<double>& g,
             double eta) {
    for (std::size_t i = 0; i < w.size(); ++i) {
        w[i] -= eta * g[i];
    }
}
