r"""Transformation between two representations of a signal on the sphere.

.. math:: f: S^2 \longrightarrow \mathbb{R}

is a signal on the sphere.

It can be decomposed into the basis of the spherical harmonics:

.. math:: f(x) = \sum_{l=0}^{l_{\mathit{max}}} F^l \cdot Y^l(x)

it is made of :math:`(l_{\mathit{max}} + 1)^2` real numbers represented in the above formula by the familly of vectors
:math:`F^l \in \mathbb{R}^{2l+1}`.

Another representation is the discretization around the sphere. For this representation we chose a particular grid of size
:math:`(N, M)`

.. math::

    x_{ij} &= (\sin(\beta_i) \sin(\alpha_j), \cos(\beta_i), \sin(\beta_i) \cos(\alpha_j))

    \beta_i &= \pi (i + 0.5) / N

    \alpha_j &= 2 \pi j / M

In the code, :math:`N` is called ``res_beta`` and :math:`M` is ``res_alpha``.

The discrete representation is therefore

.. math:: \{ h_{ij} = f(x_{ij}) \}_{ij}
"""
from typing import Callable, List, Optional, Tuple, Union

import chex
from typing import Callable, List, Optional, Sequence, Tuple, Union

import chex
import jax
import jax.numpy as jnp
import numpy as np
import scipy.signal
import scipy.spatial

import e3nn_jax as e3nn

from .spherical_harmonics import _sh_alpha, _sh_beta


class SphericalSignal:
    grid_values: chex.Array
    quadrature: str
    p_val: int
    p_arg: int

    def __init__(self, grid_values: chex.Array, quadrature: str, p_val: int = 1, p_arg: int = -1) -> None:
        # if len(grid_values.shape) < 2:
        #     raise ValueError("Grid values of wrong shape.")

        if quadrature not in ["soft", "gausslegendre"]:
            raise ValueError(f"Invalid quadrature for SphericalSignal: {quadrature}")

        if p_val not in (-1, 1):
            raise ValueError(f"Parity p_val must be either +1 or -1. Received: {p_val}")

        if p_arg not in (-1, 1):
            raise ValueError(f"Parity p_arg must be either +1 or -1. Received: {p_arg}")

        self.grid_values = grid_values
        self.quadrature = quadrature
        self.p_val = p_val
        self.p_arg = p_arg

    def __mul__(self, scalar: float) -> "SphericalSignal":
        """Multiply SphericalSignal by a scalar."""
        return SphericalSignal(self.grid_values * scalar, self.quadrature, self.p_val, self.p_arg)

    def __rmul__(self, scalar: float) -> "SphericalSignal":
        """Multiply SphericalSignal by a scalar."""
        return SphericalSignal(self.grid_values * scalar, self.quadrature, self.p_val, self.p_arg)

    def __add__(self, other: "SphericalSignal") -> "SphericalSignal":
        """Add to another SphericalSignal."""
        if self.grid_resolution != other.grid_resolution:
            raise ValueError(
                "Grid resolutions for both signals must be identical. "
                "Use .resample() to change one of the grid resolutions."
            )
        if (self.p_val, self.p_arg) != (other.p_val, other.p_arg):
            raise ValueError("Parity for both signals must be identical.")
        if self.quadrature != other.quadrature:
            raise ValueError("Quadrature for both signals must be identical.")

        return SphericalSignal(self.grid_values + other.grid_values, self.quadrature, self.p_val, self.p_arg)

    def __sub__(self, other: "SphericalSignal") -> "SphericalSignal":
        """Subtract another SphericalSignal."""
        if self.grid_resolution != other.grid_resolution:
            raise ValueError(
                "Grid resolutions for both signals must be identical. "
                "Use .resample() to change one of the grid resolutions."
            )
        if (self.p_val, self.p_arg) != (other.p_val, other.p_arg):
            raise ValueError("Parity for both signals must be identical.")
        if self.quadrature != other.quadrature:
            raise ValueError("Quadrature for both signals must be identical.")

        return SphericalSignal(self.grid_values - other.grid_values, self.quadrature, self.p_val, self.p_arg)

    @property
    def shape(self) -> Tuple[int, ...]:
        """Returns the shape of this signal."""
        return self.grid_values.shape

    @property
    def dtype(self) -> jnp.dtype:
        """Returns the dtype of this signal."""
        return self.grid_values.dtype

    @property
    def grid_y(self) -> chex.Array:
        """Returns y-values on the grid for this signal."""
        y, _, _ = _s2grid(self.res_beta, self.res_alpha, self.quadrature)
        return y

    @property
    def grid_alpha(self) -> chex.Array:
        """Returns alpha values on the grid for this signal."""
        _, alpha, _ = _s2grid(self.res_beta, self.res_alpha, self.quadrature)
        return alpha

    @property
    def grid_vectors(self) -> chex.Array:
        """The points on the sphere."""
        y, alpha, _ = _s2grid(self.res_beta, self.res_alpha, self.quadrature)
        return _s2grid_vectors(y, alpha)

    @property
    def quadrature_weights(self) -> chex.Array:
        """Returns quadrature weights for this signal."""
        _, _, qw = _s2grid(self.res_beta, self.res_alpha, self.quadrature)
        return qw

    @property
    def res_beta(self) -> int:
        """Grid resolution for beta."""
        return self.grid_values.shape[-2]

    @property
    def res_alpha(self) -> int:
        """Grid resolution for alpha."""
        return self.grid_values.shape[-1]

    @property
    def grid_resolution(self) -> Tuple[int, int]:
        """Grid resolution for (beta, alpha)."""
        return (self.res_beta, self.res_alpha)

    def resample(self, grid_resolution: Tuple[int, int], lmax: int, quadrature: Optional[str] = None) -> "SphericalSignal":
        """Resamples a signal via the spherical harmonic coefficients."""
        if quadrature is None:
            quadrature = self.quadrature
        coeffs = e3nn.from_s2grid(self, s2_irreps(lmax, self.p_val, self.p_arg))
        return e3nn.to_s2grid(coeffs, *grid_resolution, quadrature=quadrature, p_val=self.p_val, p_arg=self.p_arg)

    # TODO: Add tests for this function!
    def _transform_by(self, transform_type: str, transform_kwargs: Tuple[Union[float, int], ...], lmax: int):
        """A wrapper for different transform_by functions."""
        coeffs = e3nn.from_s2grid(self, s2_irreps(lmax, self.p_val, self.p_arg))
        transforms = {
            "angles": coeffs.transform_by_angles,
            "matrix": coeffs.transform_by_matrix,
            "axis_angle": coeffs.transform_by_axis_angle,
            "quaternion": coeffs.transform_by_quaternion,
        }
        transformed_coeffs = transforms[transform_type](**transform_kwargs)
        return e3nn.to_s2grid(
            transformed_coeffs, *self.grid_resolution, quadrature=self.quadrature, p_val=self.p_val, p_arg=self.p_arg
        )

    def transform_by_angles(self, alpha: float, beta: float, gamma: float, lmax: int) -> "SphericalSignal":
        """Rotate the signal by the given Euler angles."""
        return self._transform_by("angles", transform_kwargs=dict(alpha=alpha, beta=beta, gamma=gamma), lmax=lmax)

    def transform_by_matrix(self, R: chex.Array, lmax: int) -> "SphericalSignal":
        """Rotate the signal by the given rotation matrix."""
        return self._transform_by("matrix", transform_kwargs=dict(R=R), lmax=lmax)

    def transform_by_axis_angle(self, axis: chex.Array, angle: float, lmax: int) -> "SphericalSignal":
        """Rotate the signal by the given angle around an axis."""
        return self._transform_by("axis_angle", transform_kwargs=dict(axis=axis, angle=angle), lmax=lmax)

    def transform_by_quaternion(self, q: chex.Array, lmax: int) -> "SphericalSignal":
        """Rotate the signal by the given quaternion."""
        return self._transform_by("quaternion", transform_kwargs=dict(q=q), lmax=lmax)

    def apply(self, func: Callable[[chex.Array], chex.Array]):
        """Applies a function pointwise on the grid."""
        # TODO: obtain the parity of the function and compute the new parity, see `e3nn.scalar_activation`
        return SphericalSignal(func(self.grid_values), self.quadrature)

    @staticmethod
    def _find_peaks_2d(x: np.ndarray) -> List[Tuple[int, int]]:
        """Helper for finding peaks in a 2D signal."""
        iii = []
        for i in range(x.shape[0]):
            jj, _ = scipy.signal.find_peaks(x[i, :])
            iii += [(i, j) for j in jj]

        jjj = []
        for j in range(x.shape[1]):
            ii, _ = scipy.signal.find_peaks(x[:, j])
            jjj += [(i, j) for i in ii]

        return list(set(iii).intersection(set(jjj)))

    # TODO: add tests for this function
    def find_peaks(self, lmax: int):
        r"""Locate peaks on the signal on the sphere."""
        grid_resolution = self.grid_resolution
        x1, f1 = self.grid_vectors, self.grid_values

        # Rotate signal.
        abc = np.array([jnp.pi / 2, jnp.pi / 2, jnp.pi / 2])
        rotated_signal = self.transform_by_angles(*abc, lmax=lmax)
        rotated_vectors = e3nn.IrrepsArray("1o", x1).transform_by_angles(*abc)
        x2, f2 = rotated_vectors, rotated_signal.grid_values

        ij = self._find_peaks_2d(f1)
        x1p = np.stack([x1[i, j] for i, j in ij])
        f1p = np.stack([f1[i, j] for i, j in ij])

        ij = self._find_peaks_2d(f2)
        x2p = np.stack([x2[i, j] for i, j in ij])
        f2p = np.stack([f2[i, j] for i, j in ij])

        # Union of the results
        mask = scipy.spatial.distance.cdist(x1p, x2p) < 2 * jnp.pi / max(*grid_resolution)
        x = np.concatenate([x1p[mask.sum(axis=1) == 0], x2p])
        f = np.concatenate([f1p[mask.sum(axis=1) == 0], f2p])

        return x, f

    def pad_to_plot(
        self,
        *,
        translation: Optional[jnp.ndarray] = None,
        scale_radius_by_amplitude: bool = False,
    ) -> Tuple[jnp.ndarray, jnp.ndarray]:
        r"""Postprocess the borders of a given signal to allow the plot it with plotly.

        Args:
            translation (optional): translation vector
            scale_radius_by_amplitude (bool): to rescale the output vectors with the amplitude of the signal

        Returns:
            r (jnp.ndarray): vectors on the sphere, shape ``(res_beta + 2, res_alpha + 1, 3)``
            f (jnp.ndarray): padded signal, shape ``(res_beta + 2, res_alpha + 1)``
        """
        f, y, alpha = self.grid_values, self.grid_y, self.grid_alpha

        # y: [-1, 1]
        one = jnp.ones_like(y, shape=(1,))
        ones = jnp.ones_like(f, shape=(1, len(alpha)))
        y = jnp.concatenate([-one, y, one])  # [res_beta + 2]
        f = jnp.concatenate([jnp.mean(f[0]) * ones, f, jnp.mean(f[-1]) * ones], axis=0)  # [res_beta + 2, res_alpha]

        # alpha: [0, 2pi]
        alpha = jnp.concatenate([alpha, alpha[:1]])  # [res_alpha + 1]
        f = jnp.concatenate([f, f[:, :1]], axis=1)  # [res_beta + 2, res_alpha + 1]

        r = _s2grid_vectors(y, alpha)  # [res_beta + 2, res_alpha + 1, 3]

        if scale_radius_by_amplitude:
            r = r * jnp.abs(f)[:, :, None]

        if translation is not None:
            r = r + translation

        return r, f

    def plotly_surface(self, translation: chex.Array, scale_radius_by_amplitude: bool = True):
        # TODO: test this function (because it does not work)
        y, alpha, _ = e3nn.s2grid(*self.grid_resolution, quadrature=self.quadrature)
        r, f = self.pad_to_plot(translation=translation, scale_radius_by_amplitude=scale_radius_by_amplitude)
        return dict(
            x=r[:, :, 0],
            y=r[:, :, 1],
            z=r[:, :, 2],
            surfacecolor=f,
        )

    # TODO: add `integral` method to compute the integral of the signal on the sphere


jax.tree_util.register_pytree_node(
    SphericalSignal,
    lambda x: ((x.grid_values,), (x.quadrature, x.p_val, x.p_arg)),
    lambda aux, grid_values: SphericalSignal(grid_values=grid_values[0], quadrature=aux[0], p_val=aux[1], p_arg=aux[2]),
)


def _s2grid_vectors(y: chex.Array, alpha: chex.Array) -> chex.Array:
    return jnp.stack(
        [
            jnp.sqrt(1.0 - y[:, None] ** 2) * jnp.sin(alpha),
            y[:, None] * jnp.ones_like(alpha),
            jnp.sqrt(1.0 - y[:, None] ** 2) * jnp.cos(alpha),
        ],
        axis=2,
    )


def sum_of_diracs(positions: chex.Array, values: chex.Array, lmax: int, p_val: int, p_arg: int) -> e3nn.IrrepsArray:
    r"""Sum of (almost-)Dirac deltas

    .. math::

        f(x) = \sum_i v_i \delta^L(\vec r_i)

    where :math:`\delta^L` is the approximation of a Dirac delta.
    """
    values = values[..., None]
    positions, _ = jnp.broadcast_arrays(positions, values)
    irreps = s2_irreps(lmax, p_val, p_arg)
    y = e3nn.spherical_harmonics(irreps, positions, normalize=True, normalization="integral")  # [..., N, dim]
    return e3nn.sum(4 * jnp.pi / (lmax + 1) ** 2 * (y * values), axis=-2)


def _quadrature_weights_soft(b: int) -> np.ndarray:
    r"""function copied from ``lie_learn.spaces.S3``
    Compute quadrature weights for the grid used by Kostelec & Rockmore [1, 2].
    """
    assert b % 2 == 0, "res_beta needs to be even for soft quadrature weights to be computed properly"
    k = np.arange(b // 2)
    return np.array(
        [
            (
                (4.0 / b)
                * np.sin(np.pi * (2.0 * j + 1.0) / (2.0 * b))
                * ((1.0 / (2 * k + 1)) * np.sin((2 * j + 1) * (2 * k + 1) * np.pi / (2.0 * b))).sum()
            )
            for j in np.arange(b)
        ],
    )


def _s2grid(res_beta: int, res_alpha: int, *, quadrature: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    r"""grid on the sphere
    Args:
        res_beta (int): :math:`N`
        res_alpha (int): :math:`M`
        quadrature (str): "soft" or "gausslegendre"

    Returns:
        y (`numpy.ndarray`): array of shape ``(res_beta)``
        alpha (`numpy.ndarray`): array of shape ``(res_alpha)``
        qw (`numpy.ndarray`): array of shape ``(res_beta)``, ``sum(qw) = 1``
    """

    if quadrature == "soft":
        i = np.arange(res_beta)
        betas = (i + 0.5) / res_beta * np.pi
        y = -np.cos(betas)  # minus sign is here to go from -1 to 1 in both quadratures

        qw = _quadrature_weights_soft(res_beta)
    elif quadrature == "gausslegendre":
        y, qw = np.polynomial.legendre.leggauss(res_beta)
    else:
        raise Exception("quadrature needs to be 'soft' or 'gausslegendre'")

    qw /= 2.0
    i = np.arange(res_alpha)
    alpha = i / res_alpha * 2 * np.pi
    return y, alpha, qw


def _spherical_harmonics_s2grid(lmax: int, res_beta: int, res_alpha: int, *, quadrature: str, dtype: np.dtype = np.float32):
    r"""spherical harmonics evaluated on the grid on the sphere
    .. math::
        f(x) = \sum_{l=0}^{l_{\mathit{max}}} F^l \cdot Y^l(x)
        f(\beta, \alpha) = \sum_{l=0}^{l_{\mathit{max}}} F^l \cdot S^l(\alpha) P^l(\cos(\beta))
    Args:
        lmax (int): :math:`l_{\mathit{max}}`
        res_beta (int): :math:`N`
        res_alpha (int): :math:`M`
        quadrature (str): "soft" or "gausslegendre"

    Returns:
        y (`jax.numpy.ndarray`): array of shape ``(res_beta)``
        alphas (`jax.numpy.ndarray`): array of shape ``(res_alpha)``
        sh_y (`jax.numpy.ndarray`): array of shape ``(res_beta, (lmax + 1)(lmax + 2)/2)``
        sh_alpha (`jax.numpy.ndarray`): array of shape ``(res_alpha, 2 * lmax + 1)``
        qw (`jax.numpy.ndarray`): array of shape ``(res_beta)``
    """
    y, alphas, qw = _s2grid(res_beta, res_alpha, quadrature=quadrature)
    y, alphas, qw = jax.tree_util.tree_map(lambda x: jnp.asarray(x, dtype), (y, alphas, qw))
    sh_alpha = _sh_alpha(lmax, alphas)  # [..., 2 * l + 1]
    sh_y = _sh_beta(lmax, y)  # [..., (lmax + 1) * (lmax + 2) // 2]
    return y, alphas, sh_y, sh_alpha, qw


def s2_irreps(lmax: int, p_val: int = 1, p_arg: int = -1) -> e3nn.Irreps:
    """Returns all Irreps upto l = lmax and of the required parity."""
    return e3nn.Irreps([(1, (l, p_val * p_arg**l)) for l in range(lmax + 1)])


def _check_parities(irreps: e3nn.Irreps, p_val: Optional[int] = None, p_arg: Optional[int] = None) -> Tuple[int, int]:
    p_even = {ir.p for mul, ir in irreps if ir.l % 2 == 0}
    p_odd = {ir.p for mul, ir in irreps if ir.l % 2 == 1}
    if not (p_even in [{1}, {-1}, set()] and p_odd in [{1}, {-1}, set()]):
        raise ValueError("irrep parities should be of the form (p_val * p_arg**l) for all l, where p_val and p_arg are ±1")

    p_even = p_even.pop() if p_even else None
    p_odd = p_odd.pop() if p_odd else None

    if p_val is not None and p_arg is not None:
        if not (p_even in [p_val, None] and p_odd in [p_val * p_arg, None]):
            raise ValueError(
                f"irrep ({irreps}) parities are not compatible with the given p_val ({p_val}) and p_arg ({p_arg})."
            )
        return p_val, p_arg

    if p_val is not None:
        if p_even is None:
            p_even = p_val
        if p_even != p_val:
            raise ValueError(f"irrep ({irreps}) parities are not compatible with the given p_val ({p_val}).")

    if p_arg is not None:
        if p_odd is None and p_even is not None:
            p_odd = p_even * p_arg
        elif p_odd is not None and p_even is None:
            p_even = p_odd * p_arg
        elif p_odd is not None and p_even is not None:
            if p_odd != p_even * p_arg:
                raise ValueError(f"irrep ({irreps}) parities are not compatible with the given p_arg ({p_arg}).")

    if p_even is not None and p_odd is not None:
        return p_even, p_even * p_odd

    return p_even, None


def from_s2grid(
    x: SphericalSignal,
    irreps: e3nn.Irreps,
    *,
    normalization: str = "component",
    lmax_in: Optional[int] = None,
    fft: bool = True,
):
    r"""Transform signal on the sphere into spherical harmonics coefficients.

    The output has degree :math:`l` between 0 and lmax, and parity :math:`p = p_{val}p_{arg}^l`

    The inverse transformation of :func:`e3nn_jax.to_s2grid`

    Args:
        x (`SphericalSignal`): signal on the sphere of shape ``(..., y/beta, alpha)``
        irreps (e3nn.Irreps): irreps of the coefficients
        normalization ({'norm', 'component', 'integral'}): normalization of the spherical harmonics basis
        lmax_in (int, optional): maximum degree of the input signal, only used for normalization purposes
        fft (bool): True if we use FFT, False if we use the naive implementation

    Returns:
        `e3nn_jax.IrrepsArray`: coefficient array of shape ``(..., (lmax+1)^2)``
    """
    res_beta, res_alpha = x.grid_resolution

    irreps = e3nn.Irreps(irreps)

    if not all(mul == 1 for mul, _ in irreps.regroup()):
        raise ValueError("multiplicities should be ones")

    _check_parities(irreps, x.p_val, x.p_arg)

    lmax = max(irreps.ls)

    if lmax_in is None:
        lmax_in = lmax

    _, _, sh_y, sha, qw = _spherical_harmonics_s2grid(lmax, res_beta, res_alpha, quadrature=x.quadrature, dtype=x.dtype)
    # sh_y: (res_beta, (l+1)(l+2)/2)

    # normalize such that it is the inverse of ToS2Grid
    n = None
    # lmax_in = max frequency in input; lmax = max freq in output
    if normalization == "component":
        n = jnp.sqrt(4 * jnp.pi) * jnp.asarray([jnp.sqrt(2 * l + 1) for l in range(lmax + 1)], x.dtype) * jnp.sqrt(lmax_in + 1)
    elif normalization == "norm":
        n = jnp.sqrt(4 * jnp.pi) * jnp.ones(lmax + 1, x.dtype) * jnp.sqrt(lmax_in + 1)
    elif normalization == "integral":
        n = 4 * jnp.pi * jnp.ones(lmax + 1, x.dtype)
    else:
        raise Exception("normalization needs to be 'norm', 'component' or 'integral'")

    # prepare beta integrand
    m_in = jnp.asarray(_expand_matrix(range(lmax + 1)), x.dtype)  # [l, m, j]
    m_out = jnp.asarray(_expand_matrix(irreps.ls), x.dtype)  # [l, m, i]
    sh_y = _rollout_sh(sh_y, lmax)
    sh_y = jnp.einsum("lmj,bj,lmi,l,b->mbi", m_in, sh_y, m_out, n, qw)  # [m, b, i]

    # integrate over alpha
    if fft:
<<<<<<< HEAD
        int_a = _rfft(x.grid_values, lmax) / res_alpha  # [..., res_beta, 2*l+1]
        int_a = rfft(x.grid_values, lmax) / res_alpha  # [..., res_beta, 2*l+1]
    else:
        int_a = jnp.einsum("...ba,am->...bm", x.grid_values, sha) / res_alpha  # [..., res_beta, 2*l+1]
        int_a = jnp.einsum("...ba,am->...bm", x.grid_values, sha) / res_alpha  # [..., res_beta, 2*l+1]
=======
        int_a = rfft(x.grid_values, lmax) / res_alpha  # [..., res_beta, 2*l+1]
    else:
        int_a = jnp.einsum("...ba,am->...bm", x.grid_values, sha) / res_alpha  # [..., res_beta, 2*l+1]
>>>>>>> 1cb0ad2 (to_s2grid() and from_s2grid() now work with SphericalSignals.)

    # integrate over beta
    int_b = jnp.einsum("mbi,...bm->...i", sh_y, int_a)  # [..., irreps]

    # convert to IrrepsArray
    return e3nn.IrrepsArray(irreps, int_b)


def _normalization(lmax: int, normalization: str, dtype) -> jnp.ndarray:
    if normalization == "component":
        # normalize such that all l has the same variance on the sphere
        # given that all component has mean 0 and variance 1
        return (
            jnp.sqrt(4 * jnp.pi) * jnp.asarray([1 / jnp.sqrt(2 * l + 1) for l in range(lmax + 1)], dtype) / jnp.sqrt(lmax + 1)
        )
    if normalization == "norm":
        # normalize such that all l has the same variance on the sphere
        # given that all component has mean 0 and variance 1/(2L+1)
        return jnp.sqrt(4 * jnp.pi) * jnp.ones(lmax + 1, dtype) / jnp.sqrt(lmax + 1)
    if normalization == "integral":
        return jnp.ones(lmax + 1, dtype)

    raise Exception("normalization needs to be 'norm', 'component' or 'integral'")


def to_s2grid(
    coeffs: e3nn.IrrepsArray,
    res_beta: int,
    res_alpha: int,
    *,
    normalization: str = "component",
    quadrature: str,
    fft: bool = True,
    p_val: Optional[int] = None,
    p_arg: Optional[int] = None,
) -> SphericalSignal:
    r"""Sample a signal on the sphere given by the coefficient in the spherical harmonics basis.

    The inverse transformation of :func:`e3nn_jax.from_s2grid`

    Args:
        coeffs (`e3nn_jax.IrrepsArray`): coefficient array
        res_beta (int): number of points on the sphere in the :math:`\theta` direction
        res_alpha (int): number of points on the sphere in the :math:`\phi` direction
        normalization ({'norm', 'component', 'integral'}): normalization of the basis
        quadrature (str): "soft" or "gausslegendre"
        fft (bool): True if we use FFT, False if we use the naive implementation
        p_val (int, optional): parity of the value of the signal
        p_arg (int, optional): parity of the argument of the signal

    Returns:
        `SphericalSignal`: signal on the sphere of shape ``(..., y/beta, alpha)``
    """
    coeffs = coeffs.regroup()
    lmax = coeffs.irreps.ls[-1]

    if not all(mul == 1 for mul, _ in coeffs.irreps):
        raise ValueError(f"Multiplicities should be ones. Got {coeffs.irreps}.")

    if (p_val is not None) != (p_arg is not None):
        raise ValueError("p_val and p_arg should be both None or both not None.")

    p_val, p_arg = _check_parities(coeffs.irreps, p_val, p_arg)

    if p_val is None or p_arg is None:
        raise ValueError(f"p_val and p_arg cannot be determined from the irreps {coeffs.irreps}, please specify them.")

    _, _, sh_y, sha, _ = _spherical_harmonics_s2grid(lmax, res_beta, res_alpha, quadrature=quadrature, dtype=coeffs.dtype)

    n = _normalization(lmax, normalization, coeffs.dtype)

    m_in = jnp.asarray(_expand_matrix(range(lmax + 1)), coeffs.dtype)  # [l, m, j]
    m_out = jnp.asarray(_expand_matrix(coeffs.irreps.ls), coeffs.dtype)  # [l, m, i]
    # put beta component in summable form
    sh_y = _rollout_sh(sh_y, lmax)
    sh_y = jnp.einsum("lmj,bj,lmi,l->mbi", m_in, sh_y, m_out, n)  # [m, b, i]

    # multiply spherical harmonics by their coefficients
    signal_b = jnp.einsum("mbi,...i->...bm", sh_y, coeffs.array)  # [batch, beta, m]

    if fft:
        if res_alpha % 2 == 0:
            raise ValueError("res_alpha must be odd for fft")

        signal = _irfft(signal_b, res_alpha) * res_alpha  # [..., res_beta, res_alpha]
    else:
        signal = jnp.einsum("...bm,am->...ba", signal_b, sha)  # [..., res_beta, res_alpha]

<<<<<<< HEAD
=======
    # Compute the parity of the irreps, and check that they are consistent with user input.
    def _extract_element(seq: Sequence[int]) -> int:
        """Extracts the first element of the sequence, otherwise None."""
        for e in seq:
            return e
        return None

    def _compute_parities(p_even: Optional[int], p_odd: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
        """Maps (p_even, p_odd) -> (p_val, p_arg)."""
        computed_p_val = p_even
        try:
            computed_p_arg = p_even * p_odd
        except TypeError:
            computed_p_arg = None
        return computed_p_val, computed_p_arg

    def _check_consistency(provided_val: Optional[int], computed_val: Optional[int], name: str) -> int:
        """Checks that the provided value and the computed value are consistent."""
        # Exactly one of the values is not None?
        # Then, we return the non-None value.
        no_check = (provided_val is None) ^ (computed_val is None)
        if no_check:
            return provided_val or computed_p_val

        # Both are None?
        # Then, we must error out.
        if provided_val is None:
            raise ValueError(f"Could not compute a value for {name}. Please provide a value.")

        # Both are not None.
        # Then, both of the values should be equal.
        if provided_val != computed_val:
            raise ValueError(f"Provided parity {name} {p_val} inconsistent with {p_val} computed from coeffs.")
        return provided_val

    p_even = _extract_element({ir.p for _, ir in coeffs.irreps if ir.l % 2 == 0})
    p_odd = _extract_element({ir.p for _, ir in coeffs.irreps if ir.l % 2 == 1})
    computed_p_val, computed_p_arg = _compute_parities(p_even, p_odd)
    p_val = _check_consistency(p_val, computed_p_val, "p_val")
    p_arg = _check_consistency(p_arg, computed_p_arg, "p_arg")
>>>>>>> 1cb0ad2 (to_s2grid() and from_s2grid() now work with SphericalSignals.)
    return SphericalSignal(signal, quadrature=quadrature, p_val=p_val, p_arg=p_arg)


def to_s2point(
    coeffs: e3nn.IrrepsArray,
    point: e3nn.IrrepsArray,
    *,
    normalization: str = "component",
) -> e3nn.IrrepsArray:
    """Evaluate a signal on the sphere given by the coefficient in the spherical harmonics basis.

    It computes the same thing as :func:`e3nn_jax.to_s2grid` but at a single point.

    Args:
        coeffs (`e3nn_jax.IrrepsArray`): coefficient array of shape ``(*shape1, irreps)``
        point (`jax.numpy.ndarray`): point on the sphere of shape ``(*shape2, 3)``
        normalization ({'norm', 'component', 'integral'}): normalization of the basis

    Returns:
        `jax.numpy.ndarray`: the value of the signal at the point, of shape ``(*shape1, *shape2, irreps)``
    """
    coeffs = coeffs.regroup()

    if not all(mul == 1 for mul, _ in coeffs.irreps):
        raise ValueError(f"Multiplicities should be ones. Got {coeffs.irreps}.")

    if not isinstance(point, e3nn.IrrepsArray):
        raise TypeError(f"point should be an e3nn.IrrepsArray, got {type(point)}.")

    if point.irreps not in ["1e", "1o"]:
        raise ValueError(f"point should be of irreps '1e' or '1o', got {point.irreps}.")

    p_arg = point.irreps[0].ir.p
    p_val, _ = _check_parities(coeffs.irreps, None, p_arg)

    sh = e3nn.spherical_harmonics(coeffs.irreps.ls, point, True, "integral")  # [*shape2, irreps]
    n = _normalization(sh.irreps.lmax, normalization, coeffs.dtype)[jnp.array(sh.irreps.ls)]  # [num_irreps]
    sh = sh * n

    shape1 = coeffs.shape[:-1]
    coeffs = coeffs.reshape((-1, coeffs.shape[-1]))
    shape2 = point.shape[:-1]
    sh = sh.reshape((-1, sh.shape[-1]))

    return e3nn.IrrepsArray(
        {1: "0e", -1: "0o"}[p_val], jnp.einsum("ai,bi->ab", sh.array, coeffs.array).reshape(shape1 + shape2 + (1,))
    )
    # Compute the parity of the irreps, and check that they are consistent with user input.
    def _extract_element(seq: Sequence[int]) -> int:
        """Extracts the first element of the sequence, otherwise None."""
        for e in seq:
            return e
        return None

    def _compute_parities(p_even: Optional[int], p_odd: Optional[int]) -> Tuple[Optional[int], Optional[int]]:
        """Maps (p_even, p_odd) -> (p_val, p_arg)."""
        computed_p_val = p_even
        try:
            computed_p_arg = p_even * p_odd
        except TypeError:
            computed_p_arg = None
        return computed_p_val, computed_p_arg

    def _check_consistency(provided_val: Optional[int], computed_val: Optional[int], name: str) -> int:
        """Checks that the provided value and the computed value are consistent."""
        # Exactly one of the values is not None?
        # Then, we return the non-None value.
        no_check = (provided_val is None) ^ (computed_val is None)
        if no_check:
            return provided_val or computed_p_val

        # Both are None?
        # Then, we must error out.
        if provided_val is None:
            raise ValueError(f"Could not compute a value for {name}. Please provide a value.")

        # Both are not None.
        # Then, both of the values should be equal.
        if provided_val != computed_val:
            raise ValueError(f"Provided parity {name} {p_val} inconsistent with {p_val} computed from coeffs.")
        return provided_val

    p_even = _extract_element({ir.p for _, ir in coeffs.irreps if ir.l % 2 == 0})
    p_odd = _extract_element({ir.p for _, ir in coeffs.irreps if ir.l % 2 == 1})
    computed_p_val, computed_p_arg = _compute_parities(p_even, p_odd)
    p_val = _check_consistency(p_val, computed_p_val, "p_val")
    p_arg = _check_consistency(p_arg, computed_p_arg, "p_arg")
    return SphericalSignal(signal, quadrature=quadrature, p_val=p_val, p_arg=p_arg)


def to_s2point(
    coeffs: e3nn.IrrepsArray,
    point: e3nn.IrrepsArray,
    *,
    normalization: str = "component",
) -> e3nn.IrrepsArray:
    """Evaluate a signal on the sphere given by the coefficient in the spherical harmonics basis.

    It computes the same thing as :func:`e3nn_jax.to_s2grid` but at a single point.

    Args:
        coeffs (`e3nn_jax.IrrepsArray`): coefficient array of shape ``(*shape1, irreps)``
        point (`jax.numpy.ndarray`): point on the sphere of shape ``(*shape2, 3)``
        normalization ({'norm', 'component', 'integral'}): normalization of the basis

    Returns:
        `jax.numpy.ndarray`: the value of the signal at the point, of shape ``(*shape1, *shape2, irreps)``
    """
    coeffs = coeffs.regroup()

    if not all(mul == 1 for mul, _ in coeffs.irreps):
        raise ValueError(f"Multiplicities should be ones. Got {coeffs.irreps}.")

    if not isinstance(point, e3nn.IrrepsArray):
        raise TypeError(f"point should be an e3nn.IrrepsArray, got {type(point)}.")

    if point.irreps not in ["1e", "1o"]:
        raise ValueError(f"point should be of irreps '1e' or '1o', got {point.irreps}.")

    p_arg = point.irreps[0].ir.p
    p_val, _ = _check_parities(coeffs.irreps, None, p_arg)

    sh = e3nn.spherical_harmonics(coeffs.irreps.ls, point, True, "integral")  # [*shape2, irreps]
    n = _normalization(sh.irreps.lmax, normalization, coeffs.dtype)[jnp.array(sh.irreps.ls)]  # [num_irreps]
    sh = sh * n

    shape1 = coeffs.shape[:-1]
    coeffs = coeffs.reshape((-1, coeffs.shape[-1]))
    shape2 = point.shape[:-1]
    sh = sh.reshape((-1, sh.shape[-1]))

    return e3nn.IrrepsArray(
        {1: "0e", -1: "0o"}[p_val], jnp.einsum("ai,bi->ab", sh.array, coeffs.array).reshape(shape1 + shape2 + (1,))
    )


def _rfft(x: jnp.ndarray, l: int) -> jnp.ndarray:
    r"""Real fourier transform
    Args:
        x (`jax.numpy.ndarray`): input array of shape ``(..., res_beta, res_alpha)``
        l (int): value of `l` for which the transform is being run
    Returns:
        `jax.numpy.ndarray`: transformed values - array of shape ``(..., res_beta, 2*l+1)``
    """
    x_reshaped = x.reshape((-1, x.shape[-1]))
    x_transformed_c = jnp.fft.rfft(x_reshaped)  # (..., 2*l+1)
    x_transformed = jnp.concatenate(
        [
            jnp.flip(jnp.imag(x_transformed_c[..., 1 : l + 1]), -1) * -jnp.sqrt(2),
            jnp.real(x_transformed_c[..., :1]),
            jnp.real(x_transformed_c[..., 1 : l + 1]) * jnp.sqrt(2),
        ],
        axis=-1,
    )
    return x_transformed.reshape((*x.shape[:-1], 2 * l + 1))


def _irfft(x: jnp.ndarray, res: int) -> jnp.ndarray:
    r"""Inverse of the real fourier transform
    Args:
        x (`jax.numpy.ndarray`): array of shape ``(..., 2*l + 1)``
        res (int): output resolution, has to be an odd number
    Returns:
        `jax.numpy.ndarray`: positions on the sphere, array of shape ``(..., res)``
    """
    assert res % 2 == 1

    l = (x.shape[-1] - 1) // 2
    x_reshaped = jnp.concatenate(
        [
            x[..., l : l + 1],
            (x[..., l + 1 :] + jnp.flip(x[..., :l], -1) * -1j) / jnp.sqrt(2),
            jnp.zeros((*x.shape[:-1], l), x.dtype),
        ],
        axis=-1,
    ).reshape((-1, x.shape[-1]))
    x_transformed = jnp.fft.irfft(x_reshaped, res)
    return x_transformed.reshape((*x.shape[:-1], x_transformed.shape[-1]))


def _expand_matrix(ls: List[int]) -> np.ndarray:
    """
    conversion matrix between a flatten vector (L, m) like that
    (0, 0) (1, -1) (1, 0) (1, 1) (2, -2) (2, -1) (2, 0) (2, 1) (2, 2)
    and a bidimensional matrix representation like that
                    (0, 0)
            (1, -1) (1, 0) (1, 1)
    (2, -2) (2, -1) (2, 0) (2, 1) (2, 2)

    Args:
        ls: list of l values
    Returns:
        array of shape ``[l, m, l * m]``
    """
    lmax = max(ls)
    m = np.zeros((lmax + 1, 2 * lmax + 1, sum(2 * l + 1 for l in ls)), np.float64)
    i = 0
    for l in ls:
        m[l, lmax - l : lmax + l + 1, i : i + 2 * l + 1] = np.eye(2 * l + 1, dtype=np.float64)
        i += 2 * l + 1
    return m


def _rollout_sh(m: jnp.ndarray, lmax: int) -> jnp.ndarray:
    """
    Expand spherical harmonic representation.
    Args:
        m: jnp.ndarray of shape (..., (lmax+1)*(lmax+2)/2)
    Returns:
        jnp.ndarray of shape (..., (lmax+1)**2)
    """
    assert m.shape[-1] == (lmax + 1) * (lmax + 2) // 2
    m_full = jnp.zeros((*m.shape[:-1], (lmax + 1) ** 2), dtype=m.dtype)
    for l in range(lmax + 1):
        i_mid = l**2 + l
        for i in range(l + 1):
            m_full = m_full.at[..., i_mid + i].set(m[..., l * (l + 1) // 2 + i])
            m_full = m_full.at[..., i_mid - i].set(m[..., l * (l + 1) // 2 + i])
    return m_full
<<<<<<< HEAD
=======


def s2grid_vectors(y: jnp.ndarray, alpha: jnp.ndarray) -> jnp.ndarray:
    r"""Calculate the points on the sphere.

    Args:
        y: array with y values, shape ``(res_beta)``
        alpha: array with alpha values, shape ``(res_alpha)``

    Returns:
        r: array of vectors, shape ``(res_beta, res_alpha, 3)``
    """
    assert y.ndim == 1
    assert alpha.ndim == 1
    return jnp.stack(
        [
            jnp.sqrt(1.0 - y[:, None] ** 2) * jnp.sin(alpha),
            y[:, None] * jnp.ones_like(alpha),
            jnp.sqrt(1.0 - y[:, None] ** 2) * jnp.cos(alpha),
        ],
        axis=2,
    )
>>>>>>> 1cb0ad2 (to_s2grid() and from_s2grid() now work with SphericalSignals.)
