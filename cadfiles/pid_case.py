"""Enclosure for the PID temperature controller: a case and a lid that slides in.

Rebuilt from the Onshape exports case.step and lid.step. X is the width, Y the
length and Z the depth, with the front panel (where the controller pokes through)
at z = 0. The two parts are in their assembled positions.
"""

from build123d import *

pid_width = 43.81 * MM
pid_length = 90.93 * MM
pid_depth = 83.01 * MM
snug_gap = 0.25 * MM
wall_thick = 3 * MM
case_width = 60 * MM
case_length = 110 * MM
case_depth = 130 * MM

# Not in the Onshape variable table - measured from the STEP files.
cable_slot_width = 14.6767 * MM  # slot in the back wall
lid_pocket_depth = 15 * MM  # how far the lid's lip reaches below its top wall
label = "PID"
label_font = "Impact"  # the Onshape font is not installed here; this is the closest
label_height = 42.92 * MM  # cap height
label_centre = (2.42 * MM, 65.76 * MM)  # (y, z) on the -x side wall
label_depth = 0.5 * MM

# The controller's cutout, with clearance; the case is split from the lid at its top edge.
cutout_width = pid_width + 2 * snug_gap
cutout_length = pid_length + 2 * snug_gap
split_y = cutout_length / 2

mid_depth = Pos(0, 0, case_depth / 2)

# One closed box with every outside edge rounded, shared by both parts.
outer = mid_depth * Box(case_width, case_length, case_depth)
outer = fillet(outer.edges(), radius=wall_thick)
below_split = Pos(0, split_y - case_length, case_depth / 2) * Box(
    2 * case_width, 2 * case_length, 2 * case_depth
)

# --- case -------------------------------------------------------------------
cavity = mid_depth * Box(
    case_width - 2 * wall_thick,
    case_length - 2 * wall_thick,
    case_depth - 2 * wall_thick,
)
front_cutout = Pos(0, 0, wall_thick / 2) * Box(cutout_width, cutout_length, wall_thick)
cable_slot = Pos(0, 0, case_depth - wall_thick / 2) * Box(
    cable_slot_width, cutout_length, wall_thick
)

text = Text(label, font_size=10, font=label_font)
text = scale(text, by=label_height / text.bounding_box().size.Y)
text = Pos(*(-text.bounding_box().center())) * text
# On the -x wall, reading along +z with +y up when seen from outside.
label_plane = Plane(
    origin=(-case_width / 2, label_centre[0], label_centre[1]),
    x_dir=(0, 0, 1),
    z_dir=(-1, 0, 0),
)
engraving = extrude(label_plane * text, amount=-label_depth)

case = (outer - cavity - front_cutout - cable_slot) & below_split
case -= engraving

# --- lid --------------------------------------------------------------------
# A solid cap above the split, plus a lip that slides inside the case walls.
lip_width = case_width - 2 * wall_thick - 2 * snug_gap
lip_depth = case_depth - 2 * wall_thick - 2 * snug_gap
pocket_top = case_length / 2 - wall_thick
lip_bottom = pocket_top - lid_pocket_depth

across = Plane.XZ.offset(-split_y)  # the split plane, normal pointing down (-y)
lip = extrude(
    across * Pos(0, case_depth / 2) * RectangleRounded(lip_width, lip_depth, wall_thick / 2),
    amount=split_y - lip_bottom,
)
# The pocket leaves a wall on three sides of the lip; the front side stays open.
pocket = Pos(0, (pocket_top + lip_bottom) / 2, case_depth / 2 - wall_thick / 2) * Box(
    lip_width - 2 * wall_thick, lid_pocket_depth, lip_depth - wall_thick
)

lid = (outer - below_split) + lip - pocket

if __name__ == "__main__":
    import ocp_vscode as ov

    ov.show(case, lid, names=["case", "lid"])
