#include <vector>

void update_r(std::vector<double>& r,
              const std::vector<std::vector<double>>& A,
              const std::vector<double>& p,
              double alpha_k) {
    for (std::size_t i = 0; i < r.size(); ++i) {
        double Ap_i = 0.0;
        for (std::size_t j = 0; j < p.size(); ++j) {
            Ap_i += A[i][j] * p[j];
        }
        r[i] += alpha_k * Ap_i;
    }
}