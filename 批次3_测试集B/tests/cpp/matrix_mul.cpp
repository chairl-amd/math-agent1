// tests/cpp/matrix_mul.cpp — 正确版矩阵乘法（GLM 列主序）
//
// 数学定义：C = A * B，其中 C[i][j] = sum_k A[k][j] * B[i][k]
// GLM 的 mat4 为列主序：m[col][row]，因此 C[col][row] = sum_k A[k][row] * B[col][k]
#include <glm/glm.hpp>
#include <glm/gtc/matrix_transform.hpp>
#include <cmath>
#include <cstdio>

static glm::mat4 mul4(const glm::mat4& A, const glm::mat4& B) {
    glm::mat4 C(0.0f);
    for (int col = 0; col < 4; ++col)
        for (int row = 0; row < 4; ++row)
            for (int k = 0; k < 4; ++k)
                C[col][row] += A[k][row] * B[col][k];  // 列主序下的正确索引
    return C;
}

int main() {
    // 验证：两次绕 Y 轴旋转 theta 等价于绕 Y 轴旋转 2*theta
    const float theta = 0.35f;
    glm::mat4 R1 = glm::rotate(glm::mat4(1.0f), theta, glm::vec3(0, 1, 0));
    glm::mat4 R2 = glm::rotate(glm::mat4(1.0f), 2.0f * theta, glm::vec3(0, 1, 0));
    glm::mat4 C  = mul4(R1, R1);

    float max_err = 0.0f;
    for (int c = 0; c < 4; ++c)
        for (int r = 0; r < 4; ++r)
            max_err = std::fmax(max_err, std::fabs(C[c][r] - R2[c][r]));

    std::printf("max_err = %.3e\n", max_err);
    // 数学期望：R(theta) * R(theta) = R(2*theta)
    return (max_err < 1e-6f) ? 0 : 1;
}