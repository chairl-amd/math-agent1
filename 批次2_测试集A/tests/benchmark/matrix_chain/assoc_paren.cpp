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

bool same(const Mat& a, const Mat& b, const Mat& c) {
    Mat left = mul(mul(a, b), c);
    Mat right = mul(a, mul(b, c));
    for (std::size_t i = 0; i < left.size(); ++i) {
        for (std::size_t j = 0; j < left[i].size(); ++j) {
            if (left[i][j] != right[i][j]) return false;
        }
    }
    return true;
}
