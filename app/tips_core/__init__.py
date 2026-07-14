"""TIPS Planner 計算核（OS非依存）。Mac/Win スタンドアロン・将来Web版で共有。"""
from .geometry import (
    ortho_image, ice_geometry, ice_image, bend_tip, needle_path, needle_path3,
    straight_path, needle_assembly, rups_path, colapinto_path, path_tangent,
    predict_straight, predict_curve, predict_readout, fit_circle_radius, COLA_R,
    proj_mm, fan_fill_for_plane, fan_beam_for_plane,
    aim_readout, surface_geometry, best_surface_theta, snap_to_skin, probe_glyph, needle_glyph, ice_coplanarity,
    R_DEPTH, FAN_HALF, PXMM, WL_DEFAULT, WW_DEFAULT,
    CONVEX_R0, CONVEX_DEPTH, CONVEX_FAN, nrm, rot3,
)

__all__ = [
    "ortho_image", "ice_geometry", "ice_image", "bend_tip", "needle_path", "needle_path3",
    "straight_path", "needle_assembly", "rups_path", "colapinto_path", "path_tangent",
    "predict_straight", "predict_curve", "predict_readout", "fit_circle_radius", "COLA_R",
    "proj_mm", "fan_fill_for_plane", "fan_beam_for_plane",
    "aim_readout", "surface_geometry", "best_surface_theta", "snap_to_skin", "probe_glyph", "needle_glyph", "ice_coplanarity",
    "R_DEPTH", "FAN_HALF", "PXMM", "WL_DEFAULT", "WW_DEFAULT",
    "CONVEX_R0", "CONVEX_DEPTH", "CONVEX_FAN", "nrm", "rot3",
]
