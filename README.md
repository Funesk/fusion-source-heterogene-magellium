# fusion-source-heterogene-magellium

# Apport de l'IA dans la maîtrise de la transformation de données géographiques

Fusion de sources de limites administratives françaises hétérogènes pour le département de la **Haute-Garonne (D031)**.

Ce dépôt regroupe les codes et notebooks produits durant un stage réalisé chez **Magellium**, dans le cadre d'un projet portant sur l'application de l'intelligence artificielle à la transformation et à la fusion de données géographiques.

---

## Contexte

L'objectif du projet est de **fusionner trois sources hétérogènes** de limites administratives couvrant le même territoire, mais issues de producteurs, de millésimes et de logiques de saisie différents :

| Source | Producteur | Millésime |
|--------|-----------|-----------|
| BD TOPO | IGN | 2025 |
| Cadastre | DGFiP | — |
| GEOFLA | IGN | 2015 |

Ces sources décrivent en principe les mêmes frontières communales, mais divergent géométriquement (décalages, sommets non partagés, anomalies d'accrochage). Le pipeline vise à produire une **couche fusionnée topologiquement propre** à partir de ces divergences, plutôt qu'un simple empilement de géométries.

L'approche repose sur une modélisation en **primitives topologiques** (nœuds, arcs) inspirée du modèle OSM, et combine deux familles de méthodes :

- **CRH** (*Conflict Resolution on Heterogeneous data*) : apprentissage automatique **non supervisé** de type EM, qui estime la fiabilité de chaque source sans vérité terrain dans la boucle de l'algorithme.
- **En-matching** : mise en correspondance des primitives (nœuds/arcs) formulée comme un problème d'**optimisation combinatoire** (programme linéaire en nombres entiers, résolu via PuLP/CBC).

Le résultat final pour le D031 est un fichier `fusion.gpkg` topologiquement clos : **1 745 arcs, 1 157 nœuds, et les 586 communes du département correctement fermées**.

---

## Architecture du pipeline

Les fichiers s'enchaînent dans l'ordre suivant :

```
build_topology_d031.py
        │
        ▼
M_01_pivot_tables.ipynb
        │
        ▼
M_02_en_matching.ipynb
        │
        ▼
M_03_crh_zonal.ipynb        (N_ZONES = 10, KAPPA = 5)
        │
        ▼
M_04_reassemblage.ipynb     →  fusion.gpkg
```

---

## Description des fichiers

### `build_topology_d031.py`
Script de préparation initiale. Construit la **topologie** du département D031 à partir des sources brutes : extraction des primitives (nœuds et arcs) et mise en forme des données pour les étapes suivantes du pipeline. C'est le point d'entrée à exécuter en premier.

### `M_01_pivot_tables.ipynb`
Construction des **tables pivots** servant de structure de travail commune aux étapes ultérieures.


### `M_02_en_matching.ipynb`
Étape de **mise en correspondance des primitives** (en-matching) entre les différentes sources. La correspondance est posée comme un problème d'optimisation combinatoire (ILP) et résolue avec PuLP/CBC. Cette étape identifie quels nœuds/arcs des différentes sources représentent la même entité géographique réelle.

### `M_03_crh_zonal.ipynb`
Application de l'algorithme **CRH zonal** (résolution de conflits sur données hétérogènes), découpé par zones (`N_ZONES = 10`, `KAPPA = 5`). Estime de manière non supervisée la fiabilité relative de chaque source et en déduit une géométrie de consensus, zone par zone.

### `M_04_reassemblage.ipynb`
Étape finale de **réassemblage**. Recompose les résultats zonaux en une couche cohérente à l'échelle du département et produit le fichier de sortie `fusion.gpkg` topologiquement propre (arcs, nœuds, communes fermées).

### `environments.txt`
Liste des **dépendances Python** nécessaires à l'exécution du projet (voir section Installation ci-dessous).

### `.gitignore`
Exclusion des fichiers non versionnés (caches, fichiers temporaires, données volumineuses).



## Exécution

Exécuter les fichiers dans l'ordre du pipeline :

1. `build_topology_d031.py`
2. `M_01_pivot_tables.ipynb`
3. `M_02_en_matching.ipynb`
4. `M_03_crh_zonal.ipynb`
5. `M_04_reassemblage.ipynb`

La sortie finale est le fichier `fusion.gpkg`.

---

## Crédits

Travail réalisé dans le cadre d'un stage chez Magellium (SeaTech — spécialité IRIS).
