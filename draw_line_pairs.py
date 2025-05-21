import csv, os, argparse
import simplekml
#import random as rnd
from collections import OrderedDict

def load_coords(files):
    coords = {}
    for fp in files:
        with open(fp, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                coords[row['LOC']] = (float(row['LAT']), float(row['LON']))
    return coords

def draw(linepairs_csv, coord_files, kml_out):
    # 1) load coords
    coords = load_coords(coord_files)

    # 2) prepare KML
    kml = simplekml.Kml()
    # pick a bright palette: each hex is RRGGBB; simplekml wants AABBGGRR
    base = ['FF4488','22FFDD','AACCFF','FFFFAA','FFEEFF','99FFFF',
            'FFAACC','CCFF55','DDAAFF','FFFF33','FF88FF','66FFFF',
            'FFAAAA','AAFF99','44DDFF','FFFFEE','FF33FF','EEFFFF',
            'FFBB55','DDFFBB','BB66FF','FFFFBB','FFBBFF','BBFFFF']
    palette = [ '88' + bbggrr[::-1] for bbggrr in base ]  
    # (you’ll need to swap RRGGBB→BBGGRR yourself)

    group_color = OrderedDict()
    idx = 0

    # 3) loop over pairs
    with open(linepairs_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            loc1 = row['Refpnt']
            loc2 = row['Pnt']
            pt1 = coords.get(loc1)
            pt2 = coords.get(loc2)
            if not pt1 or not pt2:
                continue

            # assign a color for this first‐LOC if needed
            if loc1 not in group_color:
                group_color[loc1] = palette[idx]
                idx = (idx + 1) % len(palette)
            color = group_color[loc1]

            # draw line
            ls = kml.newlinestring(name=f"{loc1}→{loc2}")
            ls.coords = [(pt1[1], pt1[0]), (pt2[1], pt2[0])]
            if loc1 in ["K","Sb","Eb","AN","PO","RK","Wb","Ec","RC"]:
                ls.style.linestyle.width = 2.5
                ls.style.linestyle.color = "EE" + color[2:]
            else:
                ls.style.linestyle.width = 1.75
                ls.style.linestyle.color = color

    kml.save(kml_out)
    print("Wrote", kml_out)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("linepairs_csv")
    p.add_argument("kml_out")
    args = p.parse_args()
    coord_files = [
      r"data\CapPnts.csv",
      r"data\LPPnts.csv",
      r"data\trypoints.csv"
    ]
    draw(args.linepairs_csv, coord_files, args.kml_out)
