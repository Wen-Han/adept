from typing import Dict

from jax import numpy as jnp
import equinox as eqx
import jax
import numpy as np

from theory.electrostatic import get_roots_to_electrostatic_dispersion


def get_complex_frequency_table(num):
    klds = np.linspace(0.2, 0.4, 128)
    wrs = np.zeros(num)
    wis = np.zeros(num)

    for i, kld in enumerate(klds):
        ww = get_roots_to_electrostatic_dispersion(1.0, 1.0, kld)
        wrs[i] = np.real(ww)
        wis[i] = np.imag(ww)

    return wrs, wis, klds


def get_envelope(p_wL, p_wR, p_L, p_R, ax):
    return 0.5 * (jnp.tanh((ax - p_L) / p_wL) - jnp.tanh((ax - p_R) / p_wR))


class Driver(eqx.Module):
    xax: jax.Array

    def __init__(self, xax):
        super().__init__()
        self.xax = xax

    def __call__(self, this_pulse: Dict, current_time: jnp.float64):
        kk = this_pulse["k0"]
        ww = this_pulse["w0"]
        dw = this_pulse["dw0"]
        t_L = this_pulse["t_c"] - this_pulse["t_w"] * 0.5
        t_R = this_pulse["t_c"] + this_pulse["t_w"] * 0.5
        t_wL = this_pulse["t_r"]
        t_wR = this_pulse["t_r"]
        x_L = this_pulse["x_c"] - this_pulse["x_w"] * 0.5
        x_R = this_pulse["x_c"] + this_pulse["x_w"] * 0.5
        x_wL = this_pulse["x_r"]
        x_wR = this_pulse["x_r"]
        envelope_t = get_envelope(t_wL, t_wR, t_L, t_R, current_time)
        envelope_x = get_envelope(x_wL, x_wR, x_L, x_R, self.xax)

        return (
            envelope_t * envelope_x * jnp.abs(kk) * this_pulse["a0"] * jnp.sin(kk * self.xax - (ww + dw) * current_time)
        )


class PoissonSolver(eqx.Module):
    one_over_kx: jax.Array

    def __init__(self, one_over_kx):
        super().__init__()
        self.one_over_kx = one_over_kx

    def __call__(self, dn):
        return jnp.real(jnp.fft.ifft(1j * self.one_over_kx * jnp.fft.fft(dn)))


class StepAmpere(eqx.Module):
    def __init__(self):
        super().__init__()

    def __call__(self, n, u):
        return n * u


def gradient(arr, kx):
    return jnp.real(jnp.fft.ifft(1j * kx * jnp.fft.fft(arr)))


class DensityStepper(eqx.Module):
    kx: jax.Array

    def __init__(self, kx):
        super().__init__()
        self.kx = kx

    def __call__(self, n, u):
        return -u * gradient(n, self.kx) - n * gradient(u, self.kx)


class VelocityStepper(eqx.Module):
    kx: jax.Array
    wis: jax.Array
    wr_corr: jax.Array

    def __init__(self, kx, kxr, physics):
        super().__init__()
        self.kx = kx

        wrs, wis, klds = get_complex_frequency_table(128)
        wrs = jnp.array(jnp.interp(kxr, klds, wrs, left=0.0, right=wrs[-1]))

        if physics["kinetic_real_wepw"]:
            self.wr_corr = wrs / jnp.sqrt(1 + 3 * kxr**2.0)
        else:
            self.wr_corr = jnp.ones_like(kxr)

        if physics["landau_damping"]:
            self.wis = jnp.array(jnp.interp(kxr, klds, wis, left=0.0, right=wis[-1]))
        else:
            self.wis = jnp.zeros_like(kxr)

    def landau_damping_term(self, u):
        return jnp.real(jnp.fft.irfft(self.wis * jnp.fft.rfft(u)))

    def restoring_force_term(self, gradp_over_nm):
        return jnp.real(jnp.fft.irfft(self.wr_corr * jnp.fft.rfft(gradp_over_nm)))

    def __call__(self, n, u, p_over_m, q_over_m_times_e, delta):
        return (
            -u * gradient(u, self.kx)
            - self.restoring_force_term(gradient(p_over_m, self.kx) / n)
            - q_over_m_times_e
            + self.landau_damping_term(u) / (1.0 + delta**2)
        )


class EnergyStepper(eqx.Module):
    kx: jax.Array
    gamma: jnp.float64

    def __init__(self, kx, gamma):
        super().__init__()
        self.kx = kx
        self.gamma = gamma

    def __call__(self, n, u, p_over_m, q_over_m_times_e):
        # T = p / n
        # q = 0.0  # -2.0655 * n * jnp.sqrt(T) * gradient(T, dx)
        return (
            -u * gradient(p_over_m, self.kx)
            - self.gamma * p_over_m * gradient(u, self.kx)
            # + 2 * n * u * q_over_m_times_e
        )


class ParticleTrapper(eqx.Module):
    kxr: jax.Array
    # wrs: jax.Array
    wis: jax.Array
    kx: jax.Array

    def __init__(self, kxr, kx):
        super().__init__()
        self.kxr = kxr
        self.kx = kx
        wrs, wis, klds = get_complex_frequency_table(128)
        # self.wrs = jnp.array(jnp.interp(kxr, klds, wrs, left=1.0, right=wrs[-1]))
        self.wis = jnp.array(jnp.interp(kxr, klds, wis, left=0.0, right=0.0))

    def __call__(self, e, delta):
        return (
            -3.757 * gradient(delta, self.kx)
            + 100*jnp.abs(jnp.fft.irfft(jnp.fft.rfft(e, axis=0) * self.wis))
            - 0.001 * delta
        )
