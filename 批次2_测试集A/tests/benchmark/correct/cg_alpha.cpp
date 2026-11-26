#include <vector>

double cg_alpha(const std::vector<double>& r,
                const std::vector<double>& p,
                const std::vector<double>& Ap) {
    double rr = 0.0;
    double pAp = 0.0;
    for (std::size_t i = 0; i < r.size(); ++i) {
        rr += r[i] * r[i];
        pAp += p[i] * Ap[i];
    }
    return rr / pAp;
}
