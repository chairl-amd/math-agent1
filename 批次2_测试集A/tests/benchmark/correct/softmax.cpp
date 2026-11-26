#include <cmath>
#include <vector>

std::vector<double> softmax(const std::vector<double>& z) {
    std::vector<double> s(z.size());
    double total = 0.0;
    for (std::size_t j = 0; j < z.size(); ++j) {
        total += std::exp(z[j]);
    }
    for (std::size_t i = 0; i < z.size(); ++i) {
        s[i] = std::exp(z[i]) / total;
    }
    return s;
}
