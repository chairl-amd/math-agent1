#include <cmath>
#include <vector>
#include <cstddef>

double cosine(const std::vector<double>& a,
              const std::vector<double>& b) {
    double dot = 0.0;
    double na = 0.0;
    double nb = 0.0;
    for (std::size_t i = 0; i < a.size(); ++i) {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    return dot / (std::sqrt(na) + std::sqrt(nb));
}
