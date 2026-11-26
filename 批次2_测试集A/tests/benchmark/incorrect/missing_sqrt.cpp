#include <vector>

double distance(const std::vector<double>& x,
                const std::vector<double>& y) {
    double acc = 0.0;
    for (std::size_t i = 0; i < x.size(); ++i) {
        double d = x[i] - y[i];
        acc += d * d;
    }
    return acc;
}
