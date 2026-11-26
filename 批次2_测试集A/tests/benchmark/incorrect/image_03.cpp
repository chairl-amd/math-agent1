#include <vector>
#include <stdexcept>
#include <cstddef>

// LU decomposition without pivoting, following the given formulas:
//   A = L*U, L_ii = 1
//   U_ij = A_ij - sum_{k<j} L_ik * U_kj
//   L_ij = (A_ij - sum_{k<j} L_ik * U_kj) / U_jj
// Note: the summation index bound is taken as k < j (as written in the source).
struct LUResult {
    std::vector<std::vector<double>> L;
    std::vector<std::vector<double>> U;
};

LUResult lu_decompose(const std::vector<std::vector<double>>& A) {
    const std::size_t n = A.size();
    for (const auto& row : A) {
        if (row.size() != n) throw std::invalid_argument("A must be square");
    }

    std::vector<std::vector<double>> L(n, std::vector<double>(n, 0.0));
    std::vector<std::vector<double>> U(n, std::vector<double>(n, 0.0));

    for (std::size_t i = 0; i < n; ++i) {
        L[i][i] = 1.0;
    }

    for (std::size_t i = 0; i < n; ++i) {
        for (std::size_t j = 0; j < n; ++j) {
            double sum = 0.0;
            for (std::size_t k = 0; k < j; ++k) {
                sum += L[i][k] * U[k][j];
            }
            if (i <= j) {
                // U_ij = A_ij - sum_{k<j} L_ik U_kj
                U[i][j] = A[i][j] - sum;
            } else {
                // L_ij = (A_ij - sum_{k<j} L_ik U_kj) / U_jj
                if (U[j][j] == 0.0) throw std::runtime_error("zero pivot in LU decomposition");
                L[i][j] = (A[i][j] - sum) / U[j][j];
            }
        }
    }

    return {L, U};
}