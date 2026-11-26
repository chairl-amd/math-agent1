#include <vector>
#include <cstddef>

struct NSEnergyResidual {
    std::vector<double> momentum;
    double continuity;
    double energy;
};

NSEnergyResidual navier_stokes_energy_residual(
    double rho,
    const std::vector<double>& u,
    const std::vector<double>& drho_u_dt,
    const std::vector<double>& div_rho_u_u,
    const std::vector<double>& grad_p,
    double mu,
    const std::vector<double>& lap_u,
    const std::vector<double>& g,
    const std::vector<double>& div_tau,
    double div_u,
    double cp,
    double dT_dt,
    const std::vector<double>& grad_T,
    double k,
    double lap_T,
    double Phi)
{
    NSEnergyResidual r;
    r.momentum.assign(3, 0.0);

    for (std::size_t i = 0; i < 3; ++i) {
        r.momentum[i] =
            drho_u_dt[i] + div_rho_u_u[i]
            + grad_p[i]
            - mu * lap_u[i]
            - rho * g[i]
            - div_tau[i];
    }

    r.continuity = div_u;

    double u_dot_grad_T = 0.0;
    for (std::size_t i = 0; i < 3; ++i) {
        u_dot_grad_T += u[i] * grad_T[i];
    }

    r.energy = rho * cp * (dT_dt + u_dot_grad_T) - k * lap_T - Phi;

    return r;
}