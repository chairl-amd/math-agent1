#include <cmath>
#include <vector>
#include <cstddef>

std::vector<double> mlp(const std::vector<std::vector<double>>& W1,
                        const std::vector<double>& b1,
                        const std::vector<std::vector<double>>& W2,
                        const std::vector<double>& b2,
                        const std::vector<double>& x) {
    std::vector<double> h1(b1.size(), 0.0);
    for (std::size_t i = 0; i < b1.size(); ++i) {
        h1[i] = b1[i];
        for (std::size_t j = 0; j < x.size(); ++j) {
            h1[i] += W1[i][j] * x[j];
        }
    }
    std::vector<double> a1(h1.size(), 0.0);
    for (std::size_t i = 0; i < h1.size(); ++i) {
        a1[i] = h1[i] > 0.0 ? h1[i] : 0.0;
    }
    std::vector<double> h2(b2.size(), 0.0);
    for (std::size_t i = 0; i < b2.size(); ++i) {
        h2[i] = b2[i];
        for (std::size_t j = 0; j < a1.size(); ++j) {
            h2[i] += W2[i][j] * a1[j];
        }
    }
    double total = 0.0;
    for (std::size_t i = 0; i < h2.size(); ++i) {
        total += std::exp(h2[i]);
    }
    std::vector<double> y(h2.size(), 0.0);
    for (std::size_t i = 0; i < h2.size(); ++i) {
        y[i] = std::exp(h2[i]) / total;
    }
    return y;
}
