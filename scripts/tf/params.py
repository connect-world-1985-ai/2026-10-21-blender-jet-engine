"""Dimensional stations for a conceptual large turbofan with an augmentor.

Units are meters. The engine axis is +X (aft). +Z is up. Polar angle is
atan2(y, z): 0 is up, positive toward +Y. A 90 degree wedge on the lower
port side is removed in the cutaway scene.
"""

import math

# Fan annulus
FAN_TIP_R = 1.350
FAN_HUB_R = 0.430
CASE_INNER_R = 1.362

# Removed wedge (cutaway), radians. Kept metal sweeps the long way around.
CUT_A0 = math.radians(-135.0)
CUT_A1 = math.radians(-45.0)
KEEP_A0 = CUT_A1
KEEP_A1 = CUT_A0 + math.tau

# Spool shaft radii
LP_SHAFT_R0 = 0.048
LP_SHAFT_R1 = 0.090
HP_SHAFT_R0 = 0.118
HP_SHAFT_R1 = 0.152

# Core gaspath stations: (x, tip radius, hub radius), monotonic in x.
CORE = [
    (0.62, 0.600, 0.445),
    (1.65, 0.640, 0.500),
    (2.05, 0.560, 0.430),
    (2.40, 0.490, 0.370),
    (3.42, 0.405, 0.332),
    (3.62, 0.430, 0.340),
    (3.88, 0.585, 0.295),
    (4.52, 0.560, 0.315),
    (4.68, 0.530, 0.370),
    (5.22, 0.630, 0.350),
    (5.50, 0.720, 0.335),
    (6.28, 0.960, 0.300),
    (6.85, 0.620, 0.020),
]

# Bypass duct outer wall (flow surface) and core-cowl outer wall.
BYPASS_OUTER = [
    (0.35, 1.358),
    (1.40, 1.345),
    (3.20, 1.260),
    (5.20, 1.150),
    (6.55, 1.080),
]
CORE_COWL = [
    (0.50, 0.520),
    (0.85, 0.700),
    (1.70, 0.820),
    (3.10, 0.900),
    (4.70, 0.940),
    (5.90, 1.020),
    (6.55, 1.040),
]

# Nacelle lip ellipse and outer mould line (skin centreline, not the OML).
LIP_C = (-0.30, 1.470)
LIP_AX = 0.200
LIP_BR = 0.115
LIP_A0 = math.radians(-115.0)
LIP_A1 = math.radians(-310.0)

OUTER_COWL = [
    (0.15, 1.545),
    (0.90, 1.555),
    (2.20, 1.540),
    (3.80, 1.460),
    (5.30, 1.300),
    (6.40, 1.160),
    (7.18, 1.090),
]
INNER_BARREL = [
    (0.48, 1.372),
    (0.12, 1.360),
    (-0.10, 1.366),
]

# Axial modules
X_LPC = (0.72, 1.58)
X_HPC = (2.48, 3.36)
X_COMB = (3.70, 4.50)
X_HPT = (4.66, 5.18)
X_LPT = (5.48, 6.22)
X_MIX = (6.15, 6.70)
X_AB = (6.72, 7.52)
X_NOZ_HINGE = 7.52
X_NOZ_THROAT = 7.86
X_NOZ_EXIT = 8.32

N_FAN = 22
N_OGV = 34
N_LPC = 4
N_HPC = 9
N_HPT = 2
N_LPT = 4
N_SWIRLER = 18
N_PETALS = 16

SPIN_SEG_MAJOR = 96
SPIN_SEG_MINOR = 64
SPIN_SEG_CUT = 72

FRAME_START = 1
FRAME_END = 192  # 8.0 s at 24 fps
FPS = 24
