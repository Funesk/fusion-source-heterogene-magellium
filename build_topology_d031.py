

import os
from collections import defaultdict, Counter
import geopandas as gpd
from shapely.geometry import LineString, Point
from shapely.ops import unary_union, polygonize
from shapely import make_valid, STRtree

# ============================================================
#  CONFIGURATION
# ============================================================
DEPT = '31'            # departement a traiter
CRS  = 2154            # Lambert-93 (metres) — requis par tout le pipeline

OUT_DIR = r"C:\Users\rocrom\conversion_osm\04_topo_d031"

BASE_CRH = r"C:\Users\rocrom\Resolving-Conflicts-in-Heterogeneous-Data--CRH_Framework\Data"
BASE_IGN = r"C:\Users\rocrom\Stage\Data\BDTOPO_3-5_TOUSTHEMES_SHP_LAMB93_D031_2025-12-15\BDTOPO\1_DONNEES_LIVRAISON_2025-12-00073\BDT_3-5_SHP_LAMB93_D031_ED2025-12-15\ADMINISTRATIF"

SOURCES = {
    'bdtopo'  : BASE_IGN + r'\COMMUNE.shp',
    'cadastre': BASE_CRH + r'\cadastre-31-communes-shp\communes.shp',
    'geofla'  : BASE_CRH + r'\GEOFLA_2-0_COMMUNE_SHP_LAMB93_FXX_2015-07-01\GEOFLA\1_DONNEES_LIVRAISON_2015\GEOFLA_2-0_SHP_LAMB93_FR-ED151\COMMUNE\COMMUNE.SHP',
}

# Nettoyage (reconstruction de couverture) : DETECTE automatiquement, par la donnee.
# Signature d'un modele non topologique : la polygonisation des frontieres produit
# nettement plus de faces que de parties de polygones (tracés dupliques qui se
# croisent), et/ou des recouvrements existent entre communes voisines.
# NETTOYER_FORCE permet de surcharger a la main si besoin : {'masource': True/False}
NETTOYER_FORCE = {}
SEUIL_FACES    = 1.2   # faces / parties > 1.2  -> non topologique
SEUIL_OVERLAP  = 1.0   # recouvrement > 1 m^2 entre deux communes -> non topologique

KEEP_ONLY_COMMON = True   # ne garder que les communes presentes dans TOUTES les sources

CANDIDATS_INSEE = ['INSEE_COM', 'id', 'insee', 'code_insee', 'INSEE',
                   'CODE_INSEE', 'ref_insee', 'COMMUNE', 'CODE_COM']
CANDIDATS_NOM   = ['NOM', 'nom', 'NOM_COM', 'name', 'NAME', 'libelle', 'LIBELLE']

PREC = 6


def col(gdf, candidats):
    return next((c for c in candidats if c in gdf.columns), None)


def key(xy):
    return (round(xy[0], PREC), round(xy[1], PREC))


# ============================================================
#  CHARGEMENT + ALIGNEMENT DES SOURCES (repris d'extract_sources)
# ============================================================
def charger_source(nom, path):
    """Charge une source brute : filtre departement, CRS Lambert-93,
    une geometrie par commune (dissolution par INSEE)."""
    print(f"Chargement {nom:<10} : existe={os.path.exists(path)}")
    gdf = gpd.read_file(path)

    ci = col(gdf, CANDIDATS_INSEE)
    cn = col(gdf, CANDIDATS_NOM)
    if ci is None:
        raise ValueError(f"[{nom}] Colonne INSEE introuvable. Colonnes : {list(gdf.columns)}")

    gdf = gdf.copy()
    gdf['_insee'] = gdf[ci].astype(str).str.zfill(5)
    gdf['_nom']   = gdf[cn].astype(str) if cn else '?'

    # filtre departement (indispensable pour GEOFLA, livre France entiere)
    n_avant = len(gdf)
    gdf = gdf[gdf['_insee'].str.startswith(DEPT)]
    print(f"  {nom}: {n_avant} entites -> {len(gdf)} dans le departement {DEPT}")

    # CRS : tout le pipeline travaille en metres Lambert-93
    if gdf.crs is None:
        raise ValueError(f"[{nom}] CRS absent du fichier — a verifier avant de continuer.")
    if gdf.crs.to_epsg() != CRS:
        print(f"  {nom}: reprojection {gdf.crs.to_epsg()} -> {CRS}")
        gdf = gdf.to_crs(CRS)

    # une geometrie par commune (certaines livraisons ont plusieurs lignes)
    n_avant = len(gdf)
    gdf = gdf.dissolve(by='_insee', as_index=False, aggfunc={'_nom': 'first'})
    if len(gdf) != n_avant:
        print(f"  {nom}: dissolution {n_avant} lignes -> {len(gdf)} communes")

    return gdf.reset_index(drop=True)


def aligner_communes(gdfs):
    """Restreint chaque source aux communes presentes dans TOUTES les sources,
    et rapporte les ecarts (fusions de communes 2015-2025, absences, etc.)."""
    codes = {nom: set(g['_insee']) for nom, g in gdfs.items()}
    common = None
    for s in codes.values():
        common = s if common is None else (common & s)

    print(f"\n=== Alignement des communes (departement {DEPT}) ===")
    for nom, s in codes.items():
        manquantes = sorted(s - common)
        print(f"  {nom:<10}: {len(s):4d} communes | propres a un sous-ensemble : {len(manquantes)}")
        if manquantes:
            apercu = ', '.join(manquantes[:8]) + (' ...' if len(manquantes) > 8 else '')
            print(f"             ignorees : {apercu}")
    print(f"  -> {len(common)} communes communes aux {len(gdfs)} sources retenues\n")

    if not common:
        raise SystemExit("Aucune commune commune a toutes les sources.")

    if KEEP_ONLY_COMMON:
        gdfs = {nom: g[g['_insee'].isin(common)].reset_index(drop=True)
                for nom, g in gdfs.items()}
    return gdfs


# ============================================================
#  TOPOLOGISATION
# ============================================================
def besoin_nettoyage(nom, geoms):
    """Determine PAR LA MESURE si une source est non topologique (aucune
    connaissance a priori de la source). Deux tests :
      1. faces de polygonize vs parties de polygones : une partition propre
         donne ~autant de faces que de parties ; les traces dupliques du
         non-topologique en produisent beaucoup plus.
      2. recouvrements : deux communes d'une partition propre ne se
         chevauchent jamais (aire d'intersection > SEUIL_OVERLAP m^2).
    """
    if nom in NETTOYER_FORCE:
        print(f"  nettoyage force a {NETTOYER_FORCE[nom]} (NETTOYER_FORCE)")
        return NETTOYER_FORCE[nom]

    geoms_v = [make_valid(g) for g in geoms]
    n_parts = sum(len(g.geoms) if g.geom_type.startswith('Multi') else 1
                  for g in geoms_v)
    noded = unary_union([g.boundary for g in geoms_v])
    n_faces = sum(1 for _ in polygonize(noded))
    ratio = n_faces / max(n_parts, 1)

    overlap = False
    tree = STRtree(geoms_v)
    for i, g in enumerate(geoms_v):
        for j in tree.query(g):
            j = int(j)
            if j <= i:
                continue
            inter = g.intersection(geoms_v[j])
            if not inter.is_empty and inter.area > SEUIL_OVERLAP:
                overlap = True
                break
        if overlap:
            break

    verdict = (ratio > SEUIL_FACES) or overlap
    print(f"  diagnostic topologique : {n_faces} faces / {n_parts} parties "
          f"(ratio {ratio:.2f}) | recouvrements : {'oui' if overlap else 'non'} "
          f"-> nettoyage {'REQUIS' if verdict else 'inutile'}")
    return verdict


def reconstruire_couverture(geoms):
    """Force une partition propre a partir de polygones qui se chevauchent / ont des trous."""
    geoms = [make_valid(g) for g in geoms]
    tree = STRtree(geoms)
    noded = unary_union([g.boundary for g in geoms])
    faces = list(polygonize(noded))
    if not faces:
        raise ValueError("polygonize n'a produit aucune face")

    parts = defaultdict(list)
    n_overlap = n_sliver = 0
    for f in faces:
        pt = f.representative_point()
        cand = [int(i) for i in tree.query(pt)]
        dans = [i for i in cand if geoms[i].contains(pt)]
        if len(dans) == 1:
            ci = dans[0]
        elif len(dans) >= 2:
            n_overlap += 1
            ci = max(dans, key=lambda i: geoms[i].intersection(f).area)
        else:
            n_sliver += 1
            ci = int(tree.nearest(f))
        parts[ci].append(f)

    propres = [unary_union(parts[i]) if parts.get(i) else geoms[i]
               for i in range(len(geoms))]
    print(f"  couverture reconstruite : {len(faces)} faces "
          f"({n_overlap} recouvrement(s), {n_sliver} sliver(s) reattribues)")
    return propres


def atomic_segments(noded):
    if noded.is_empty:
        return []
    if noded.geom_type == 'LineString':
        return [noded]
    return [g for g in noded.geoms if g.geom_type == 'LineString']


def build_arcs(segments):
    deg = Counter()
    for s in segments:
        deg[key(s.coords[0])]  += 1
        deg[key(s.coords[-1])] += 1
    junctions = {k for k, d in deg.items() if d != 2}

    adj = defaultdict(list)
    for i, s in enumerate(segments):
        a, b = key(s.coords[0]), key(s.coords[-1])
        adj[a].append((i, b))
        adj[b].append((i, a))

    used = [False] * len(segments)
    arcs = []

    def coords_oriented(seg, start_key):
        cs = list(seg.coords)
        if key(cs[0]) != start_key:
            cs = cs[::-1]
        return cs

    for j in junctions:
        for (idx, nxt) in adj[j]:
            if used[idx]:
                continue
            used[idx] = True
            chain = coords_oriented(segments[idx], j)
            cur = nxt
            while cur not in junctions:
                suite = [(i2, o2) for (i2, o2) in adj[cur] if not used[i2]]
                if not suite:
                    break
                i2, o2 = suite[0]
                used[i2] = True
                chain.extend(coords_oriented(segments[i2], cur)[1:])
                cur = o2
            arcs.append(LineString(chain))

    for i, s in enumerate(segments):
        if not used[i]:
            used[i] = True
            arcs.append(s)

    return arcs, junctions


def arc_communes(arc, geoms, tree, eps=1e-6):
    mid = arc.interpolate(0.5, normalized=True)
    cand = [int(i) for i in tree.query(mid)]
    return [i for i in cand if geoms[i].boundary.distance(mid) < eps]


def traiter(nom, gdf):
    """Topologise une source deja chargee/alignee (GeoDataFrame, pas un chemin)."""
    print(f"\n=== {nom} ({len(gdf)} communes) ===")
    geoms = list(gdf.geometry)

    if besoin_nettoyage(nom, geoms):
        geoms = reconstruire_couverture(geoms)

    tree = STRtree(geoms)
    labels = list(gdf['_insee'])

    noded = unary_union([g.boundary for g in geoms])
    segments = atomic_segments(noded)
    arcs, junctions = build_arcs(segments)

    rows = []
    partages = Counter()
    for arc in arcs:
        cs = arc_communes(arc, geoms, tree)
        codes = sorted(labels[i] for i in cs)
        comm_a = codes[0] if len(codes) >= 1 else None
        comm_b = codes[1] if len(codes) == 2 else None
        if comm_b is not None:
            partages[(comm_a, comm_b)] += 1
        rows.append({'comm_a': comm_a, 'comm_b': comm_b,
                     'partage': comm_b is not None,
                     'longueur': round(arc.length, 2),
                     'geometry': arc})

    arcs_gdf = gpd.GeoDataFrame(rows, crs=gdf.crs)
    noeuds_gdf = gpd.GeoDataFrame(
        {'geometry': [Point(k) for k in junctions]}, crs=gdf.crs)

    n_part = int(arcs_gdf['partage'].sum())
    print(f"  noeuds (jonctions) : {len(noeuds_gdf)}")
    print(f"  arcs               : {len(arcs_gdf)}  (dont {n_part} partage(s))")
    fragmentes = {(a, b): n for (a, b), n in partages.items() if n != 1}
    print(f"    {len(partages)} paires adjacentes ; "
          f"{len(partages) - len(fragmentes)} a 1 arc, {len(fragmentes)} fragmentee(s)")
    for (a, b), n in sorted(fragmentes.items()):
        print(f"    !! {a} - {b} : {n} arcs (fragmente)")
    if n_part == 0:
        print("  !! aucun arc partage.")

    os.makedirs(OUT_DIR, exist_ok=True)
    gpkg = os.path.join(OUT_DIR, f"topo_{nom}.gpkg")
    arcs_gdf.to_file(gpkg, layer='arcs', driver='GPKG')
    noeuds_gdf.to_file(gpkg, layer='noeuds', driver='GPKG')
    print(f"  -> {gpkg}")


# ============================================================
#  EXECUTION
# ============================================================
if __name__ == '__main__':
    gdfs = {nom: charger_source(nom, path) for nom, path in SOURCES.items()}
    gdfs = aligner_communes(gdfs)
    for nom, gdf in gdfs.items():
        traiter(nom, gdf)
