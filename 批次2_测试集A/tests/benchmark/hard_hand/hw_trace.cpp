#include <vector>
#include <cstddef>

double trace(const std::vector<std::vector<double>>& a) {
    double s = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        s += a[i][i];
    }
    return s;
}
