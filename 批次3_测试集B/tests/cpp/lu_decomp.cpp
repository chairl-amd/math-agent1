// tests/cpp/lu_decomp.cpp — 带精度问题的 LU 分解（Hilbert 病态矩阵 + float + 不选主元）
//
// 数学：A = L * U；解 Ax=b 经 Ly=b 前代、Ux=y 回代。
// 精度隐患（本样例故意保留）：
//   1. 全程 float（约 7 位有效数字）；
//   2. 不选主元，小主元导致乘子放大；
//   3. Hilbert 矩阵条件数 ~1.5e4，误差被显著放大。
#include <cmath>
#include <cstdio>

constexpr int N = 4;

static void lu_decompose(float M[N][N]) {
    for (int i = 0; i < N; ++i) {
        for (int j = i + 1; j < N; ++j) {
            M[j][i] /= M[i][i];
            for (int k = i + 1; k < N; ++k)
                M[j][k] -= M[j][i] * M[i][k];
        }
    }
}

static void lu_solve(const float A[N][N], const float b[N], float x[N]) {
    float M[N][N];
    for (int i = 0; i < N; ++i)
        for (int j = 0; j < N; ++j) M[i][j] = A[i][j];
    lu_decompose(M);
    float y[N];
    for (int i = 0; i < N; ++i) {
        float s = b[i];
        for (int k = 0; k < i; ++k) s -= M[i][k] * y[k];
        y[i] = s;
    }
    for (int i = N - 1; i >= 0; --i) {
        float s = y[i];
        for (int k = i + 1; k < N; ++k) s -= M[i][k] * x[k];
        x[i] = s / M[i][i];
    }
}

int main() {
    float H[N][N];
    for (int i = 0; i < N; ++i)
        for (int j = 0; j < N; ++j) H[i][j] = 1.0f / (i + j + 1);
    const float x_true[N] = {1.0f, 1.0f, 1.0f, 1.0f};
    float b[N];
    for (int i = 0; i < N; ++i) {
        b[i] = 0.0f;
        for (int j = 0; j < N; ++j) b[i] += H[i][j] * x_true[j];
    }
    float x[N];
    lu_solve(H, b, x);
    float max_err = 0.0f;
    for (int i = 0; i < N; ++i) max_err = std::fmax(max_err, std::fabs(x[i] - x_true[i]));
    std::printf("x = [%.8f, %.8f, %.8f, %.8f]\n", x[0], x[1], x[2], x[3]);
    std::printf("max|x - x_true| = %.3e  (float eps ~ 1.2e-07)\n", max_err);
    return (max_err < 1e-4f) ? 0 : 1;
}