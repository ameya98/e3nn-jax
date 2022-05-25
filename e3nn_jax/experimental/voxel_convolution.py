import math
from typing import Tuple

import haiku as hk
import jax
import jax.numpy as jnp
from e3nn_jax import (
    FunctionalFullyConnectedTensorProduct,
    FunctionalLinear,
    Irreps,
    IrrepsData,
    soft_one_hot_linspace,
    spherical_harmonics,
)
from jax import lax


class Convolution(hk.Module):
    def __init__(
        self,
        irreps_out,
        irreps_sh,
        diameter: float,
        num_radial_basis: int,
        steps: Tuple[float, float, float],
        *,
        padding="SAME",
        irreps_in=None,
    ):
        super().__init__()

        self.irreps_in = Irreps(irreps_in) if irreps_in is not None else None
        self.irreps_out = Irreps(irreps_out)
        self.irreps_sh = Irreps(irreps_sh)
        self.diameter = diameter
        self.num_radial_basis = num_radial_basis
        self.steps = steps
        self.padding = padding

        with jax.ensure_compile_time_eval():
            r = self.diameter / 2

            s = math.floor(r / self.steps[0])
            x = jnp.arange(-s, s + 1.0) * self.steps[0]

            s = math.floor(r / self.steps[1])
            y = jnp.arange(-s, s + 1.0) * self.steps[1]

            s = math.floor(r / self.steps[2])
            z = jnp.arange(-s, s + 1.0) * self.steps[2]

            lattice = jnp.stack(jnp.meshgrid(x, y, z, indexing="ij"), axis=-1)  # [x, y, z, R^3]

            self.emb = soft_one_hot_linspace(
                jnp.linalg.norm(lattice, ord=2, axis=-1),
                start=0.0,
                end=self.diameter / 2,
                number=self.num_radial_basis,
                basis="smooth_finite",
                start_zero=True,
                end_zero=True,
            )  # [x, y, z, num_radial_basis]

            self.sh = spherical_harmonics(
                irreps_out=self.irreps_sh, input=lattice, normalize=True, normalization="component"
            )  # [x, y, z, irreps_sh.dim]

    def kernel(self, irreps_in, irreps_out) -> jnp.ndarray:
        # convolution
        tp = FunctionalFullyConnectedTensorProduct(irreps_in, self.irreps_sh, irreps_out)

        w = [
            hk.get_parameter(
                f"w[{i.i_in1},{i.i_in2},{i.i_out}] {tp.irreps_in1[i.i_in1]},{tp.irreps_in2[i.i_in2]},{tp.irreps_out[i.i_out]}",
                (self.num_radial_basis,) + i.path_shape,
                init=hk.initializers.RandomNormal(),
            )
            for i in tp.instructions
        ]
        w = [
            jnp.einsum("xyzk,k...->xyz...", self.emb, x)
            / (self.sh.shape[0] * self.sh.shape[1] * self.sh.shape[2])  # [x,y,z, tp_w]
            for x in w
        ]

        tp_right = tp.right
        for _ in range(3):
            tp_right = jax.vmap(tp_right, (0, 0), 0)
        k = tp_right(w, self.sh)  # [x,y,z, irreps_in.dim, irreps_out.dim]

        # self-connection
        lin = FunctionalLinear(irreps_in, irreps_out)
        w = [
            hk.get_parameter(
                f"self-connection[{ins.i_in},{ins.i_out}] {lin.irreps_in[ins.i_in]},{lin.irreps_out[ins.i_out]}",
                shape=ins.path_shape,
                init=hk.initializers.RandomNormal(),
            )
            for ins in lin.instructions
        ]
        # note that lattice[center] is always displacement zero
        k = k.at[k.shape[0] // 2, k.shape[1] // 2, k.shape[2] // 2].set(lin.matrix(w))
        return k

    def __call__(self, input: IrrepsData) -> IrrepsData:
        """
        input: [batch, x, y, z, irreps_in.dim]
        """
        if self.irreps_in is not None:
            input = IrrepsData.new(self.irreps_in, input)
        if not isinstance(input, IrrepsData):
            raise ValueError("Convolution: input should be of type IrrepsData")

        input = input.remove_nones().simplify()

        irreps_out = Irreps(
            [
                (mul, ir)
                for (mul, ir) in self.irreps_out
                if any(ir in ir_in * ir_sh for _, ir_in in input.irreps for _, ir_sh in self.irreps_sh)
            ]
        )

        output = IrrepsData.from_contiguous(
            irreps_out,
            lax.conv_general_dilated(
                lhs=input.contiguous,
                rhs=self.kernel(input.irreps, irreps_out),
                window_strides=(1, 1, 1),
                padding=self.padding,
                dimension_numbers=("NXYZC", "XYZIO", "NXYZC"),
            ),
        )

        if irreps_out != self.irreps_out:
            list = []
            i = 0
            for mul_ir in self.irreps_out:
                if i < len(irreps_out) and irreps_out[i] == mul_ir:
                    list.append(output.list[i])
                    i += 1
                else:
                    list.append(None)
            output = IrrepsData.from_list(self.irreps_out, list, output.shape)

        return output
