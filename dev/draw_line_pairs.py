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

def get_line_styles(line_pairs):
    # want a bright palette for GE: each hex is RRGGBB; simplekml wants AABBGGRR
    base = ['FF0088','2266DD','AA1111','7700FF','99EE11','EE1100',
            '22CCCC','11EE55','00AAFF','DD9933','AA8800','662200',
            'BBAA00','33EE00','44DDEE','FF3300','0033AA','990099',
            '00FF55','1100BB','EE6600','00AA11','88BB33','BB0033']
    palette = [ '99' + bbggrr[::-1] for bbggrr in base ]

    group_styles = OrderedDict()
    idx = 0
    for row in line_pairs:
        loc1 = row['Refpnt']
        if loc1 not in group_styles:
            # Assign individual line styles
            if loc1 in ["K","J"]:
                color = "EEEEFFFF"
                width = 2.4
            elif loc1 in ["AN","PO","Cap"]:
                color = "DDFF0088"
                width = 2.2
            elif loc1 in ["Sb", "B","Nb", "M", "P"]:
                color = 'CCAA00FF'
                width = 1.8
            elif loc1 in ["Ec","Eb","Wh","Wb","RK"]:
                color = 'BB0044FF'
                width = 1.4
            else: # Defaults
                idx = (idx + 1) % len(palette)
                color = palette[idx]
                width = 1.0
                
            group_styles[loc1] = {"color": color, "width": width}

    return group_styles

def draw(linepairs_csv, coord_files, kml_out):
    # load coords
    coords = load_coords(coord_files)

    # create kml
    kml = simplekml.Kml()
    idx = 0
    line_pairs = []
    with open(linepairs_csv, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            line_pairs.append(row)
    line_styles = get_line_styles(line_pairs)

    for row in line_pairs:
        loc1 = row['Refpnt']
        loc2 = row['Pnt']
        pt1 = coords.get(loc1)
        pt2 = coords.get(loc2)
        if not pt1 or not pt2:
            print("Problem loading line pairs. Skipping row ", row)
            continue
        ls = kml.newlinestring(name=f"{loc1}→{loc2}")
        ls.coords = [(pt1[1], pt1[0]), (pt2[1], pt2[0])]
        ls.style.linestyle.width = line_styles[loc1]["width"]
        ls.style.linestyle.color = line_styles[loc1]["color"]

    kml.save(kml_out)
    print("Wrote", kml_out)

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("linepairs_csv")
    p.add_argument("kml_out")
    args = p.parse_args()
    coord_files = [
      r"data\ref.csv",
      r"data\selected.csv"
      #r"data\trypoints.csv"
    ]
    draw(args.linepairs_csv, coord_files, args.kml_out)
