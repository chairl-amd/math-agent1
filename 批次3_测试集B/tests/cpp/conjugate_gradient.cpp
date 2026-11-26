// tests/cpp/conjugate_gradient.cpp — 共轭梯度法求解对称正定线性系统 A x = b
#include <cmath>
#include <cstdio>
#include <vector>

static float dot(const std::vector<float>& a, const std::vector<float>& b) {
    float s = 0.0f;
    for (size_t i = 0; i < a.size(); ++i) s += a[i] * b[i];
    return s;
}

static std::vector<float> matvec(const std::vector<std::vector<float>>& A,
                                 const std::vector<float>& x) {
    const size_t n = x.size();
    std::vector<float> y(n, 0.0f);
    for (size_t i = 0; i < n; ++i)
        for (size_t j = 0; j < n; ++j)
            y[i] += A[i][j] * x[j];
    return y;
}

int main() {
    const int n = 5;
    std::vector<std::vector<float>> A(n, std::vector<float>(n, 0.0f));
    for (int i = 0; i < n; ++i) {
        A[i][i] = 4.0f;
        if (i + 1 < n) { A[i][i + 1] = -1.0f; A[i + 1][i] = -1.0f; }
    }
    const std::vector<float> b = {1, 2, 3, 4, 5};
    std::vector<float> x(n, 0.0f), r = b, p = r;
    float rs_old = dot(r, r);
    const float tol = 1e-6f;
    const int max_iter = 50;

    int it = 0;
    for (; it < max_iter; ++it) {
        std::vector<float> Ap = matvec(A, p);
        float pAp = dot(p, Ap);
        if (std::fabs(pAp) < 1e-20f) break;
        float alpha = rs_old / pAp;
        for (int i = 0; i < n; ++i) { x[i] += alpha * p[i]; r[i] -= alpha * Ap[i]; }
        float rs_new = dot(r, r);
        if (std::sqrt(rs_new) < tol) { rs_old = rs_new; it += 1; break; }
        float beta = rs_new / rs_old;
        for (int i = 0; i < n; ++i) p[i] = r[i] + beta * p[i];
        rs_old = rs_new;
    }
    std::printf("iterations = %d, residual = %.3e\n", it, std::sqrt(rs_old));
    std::printf("x = [%.6f, %.6f, %.6f, %.6f, %.6f]\n",
                x[0], x[1], x[2], x[3], x[4]);
    return 0;
}