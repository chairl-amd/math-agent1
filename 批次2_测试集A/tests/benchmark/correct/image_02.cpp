#include <array>
#include <cmath>

// R_y(theta) = [[cos(theta), 0, sin(theta)],
//                [0,          1, 0          ],
//                [-sin(theta),0, cos(theta) ]]
std::array<std::array<double, 3>, 3> rotation_y(double theta) {
    double c = std::cos(theta);
    double s = std::sin(theta);
    return {{
        {{ c, 0.0,  s}},
        {{0.0, 1.0, 0.0}},
        {{-s, 0.0,  c}}
    }};
}