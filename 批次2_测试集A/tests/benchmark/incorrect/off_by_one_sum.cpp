#include <vector>

double sum_except_last(const std::vector<double>& a) {
    double s = 0.0;
    for (std::size_t i = 0; i + 1 < a.size(); ++i) {
        s += a[i];
    }
    return s;
}
