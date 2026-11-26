#include <cstddef>
#include <vector>

void conv_shift(
    const std::vector<double>& x,
    const std::vector<double>& h,
    std::vector<double>& y,
    std::size_t output_size) {
    for (std::size_t i = 0; i < output_size; ++i) {
        double sum = 0.0;
        for (std::size_t k = 0; k + 1 < i; ++k) {
            sum += x[i - k] * h[k];
        }
        y[i] = sum;
    }
}
