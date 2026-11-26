// tests/cpp/rotate_y_bug.cpp — 故意写错：绕 Y 轴旋转矩阵的行列写反
//
// 正确的 Ry(theta)（列主序 mat3，m[col][row]）：
//   [  c  0  s ]        列0 = ( c, 0, -s)
//   [  0  1  0 ]        列1 = ( 0, 1,  0)
//   [ -s  0  c ]        列2 = ( s, 0,  c)
//
// 本文件把 m[0][2] 与 m[2][0] 互换，等价于旋转 -theta（即 R 的转置）。
// 用途：作为「待校验 bug 用例」，供 LLM 提取公式 + SymPy 比对发现错误。
#include <glm/glm.hpp>
#include <cmath>
#include <cstdio>

static glm::mat3 rotateY_bug(float theta) {
    const float c = std::cos(theta);
    const float s = std::sin(theta);
    glm::mat3 R(1.0f);
    // BUG: 下面两行本应是 R[0][2] = -s; R[2][0] = s;
    R[0][2] =  s;
    R[2][0] = -s;
    R[0][0] =  c;
    R[2][2] =  c;
    return R;
}

int main() {
    const float theta = 0.5f;
    glm::mat3 Rbug = rotateY_bug(theta);

    // 用 rotateY_bug(θ) * v 与标准 Ry(θ) * v 对比
    glm::vec3 v(1.0f, 0.0f, 0.0f);
    glm::vec3 got = Rbug * v;

    const float c = std::cos(theta), s = std::sin(theta);
    glm::vec3 want(c, 0.0f, -s);  // 标准 Ry(θ) 作用于 x 轴

    float err = glm::length(got - want);
    std::printf("got=(%.4f, %.4f, %.4f) want=(%.4f, %.4f, %.4f) err=%.4f\n",
                got.x, got.y, got.z, want.x, want.y, want.z, err);
    return 0;  // 故意不返回失败：这是待检出的 bug 样本
}