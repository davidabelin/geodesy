from collections import deque

# 3D face normals as labels
NORMS = ["+X","-X","+Y","-Y","+Z","-Z"]

# 2D edge directions for the net
DIRS2 = {
    "N": (0, 1),
    "S": (0,-1),
    "E": (1, 0),
    "W": (-1,0),
}

# Rotation rules: given current face normal, what normal do you get
# when you cross an edge in the unfolded net?
#
# This table encodes the cube's geometry.
# We'll fill it carefully once you’re back so it’s guaranteed consistent.
ROT = {
    # Example placeholders:
    # ("+Z","N") -> "+Y"
}

def is_valid_net(net_adj, root=0):
    """
    net_adj: dict face -> list of (neighbor_face, direction_from_face)
             direction_from_face in {"N","S","E","W"} describing planar attachment
    """
    q = deque([root])
    pos2 = {root: (0,0)}
    norm3 = {root: "+Z"}

    used_pos = {(0,0)}
    used_norm = {"+Z"}

    while q:
        f = q.popleft()
        x,y = pos2[f]
        n = norm3[f]
        for g, d in net_adj.get(f, []):
            dx,dy = DIRS2[d]
            p = (x+dx, y+dy)
            nn = ROT[(n, d)]

            if g in pos2:
                # consistency checks
                if pos2[g] != p or norm3[g] != nn:
                    return False
                continue

            # place g
            if p in used_pos:      # 2D overlap
                return False
            if nn in used_norm:    # two faces claim same cube side
                return False

            pos2[g] = p
            norm3[g] = nn
            used_pos.add(p)
            used_norm.add(nn)
            q.append(g)

    return len(pos2) == 6 and used_norm == set(NORMS)
