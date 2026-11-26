#include <vector>
#include <cstddef>

using Mat = std::vector<std::vector<double>>;

Mat mul(const Mat& x, const Mat& y) {
    Mat z(x.size(), std::vector<double>(y[0].size(), 0.0));
    for (std::size_t i = 0; i < x.size(); ++i) {
        for (std::size_t j = 0; j < y[0].size(); ++j) {
            for (std::size_t k = 0; k < y.size(); ++k) {
                z[i][j] += x[i][k] * y[k][j];
            }
        }
    }
    return z;
}

Mat chain3(const Mat& a, const Mat& b, const Mat& c) {
    return mul(mul(a, b), c);
}
