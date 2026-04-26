"""Cluster analysis for highpoints output data.

This module provides functions to load highgrid output, perform clustering
on point distributions, and identify local maxima.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from typing import List, Optional
from sklearn.cluster import DBSCAN, KMeans, AgglomerativeClustering
from sklearn.preprocessing import StandardScaler
import matplotlib.pyplot as plt


def load_highgrid_csv(csv_path: Path, point_types: Optional[List[str]] = None) -> gpd.GeoDataFrame:
    """Load highgrid CSV output and optionally filter by point types.

    Parameters
    ----------
    csv_path : Path
        Path to the highgrid_out.csv file.
    point_types : list of str, optional
        List of point types to include ('hi', 'lo', 'avg'). If None, include all.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with point data.
    """
    df = pd.read_csv(csv_path)
    if point_types:
        df = df[df['point_type'].isin(point_types)]
    
    # Create geometry from lon, lat
    geometry = gpd.points_from_xy(df.lon, df.lat)
    gdf = gpd.GeoDataFrame(df, geometry=geometry)
    gdf.crs = "EPSG:4326"
    return gdf


def prepare_features(gdf: gpd.GeoDataFrame, dims: str = '3d', scale: bool = False) -> np.ndarray:
    """Prepare feature matrix for clustering.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Input GeoDataFrame.
    dims : str
        Dimensionality: '1d' (elevation only), '2d' (x,y), '3d' (x,y,elev).
    scale : bool
        Whether to standardize features.

    Returns
    -------
    np.ndarray
        Feature matrix.
    """
    if dims == '3d':
        X = np.array([[row.x, row.y, row.elev_m] for row in gdf.itertuples()])
    elif dims == '2d':
        X = np.array([[row.x, row.y] for row in gdf.itertuples()])
    elif dims == '1d':
        X = np.array([[row.elev_m] for row in gdf.itertuples()])
    else:
        raise ValueError(f"Invalid dims: {dims}")
    
    if scale:
        scaler = StandardScaler()
        X = scaler.fit_transform(X)
    
    return X


def cluster_points(X: np.ndarray, method: str = 'dbscan', **kwargs) -> np.ndarray:
    """Perform clustering on feature matrix.

    Parameters
    ----------
    X : np.ndarray
        Feature matrix.
    method : str
        Clustering method: 'dbscan', 'kmeans', 'agglomerative'.
    **kwargs
        Parameters for the clustering algorithm.

    Returns
    -------
    np.ndarray
        Cluster labels.
    """
    if method == 'dbscan':
        clusterer = DBSCAN(**kwargs)
    elif method == 'kmeans':
        clusterer = KMeans(**kwargs)
    elif method == 'agglomerative':
        clusterer = AgglomerativeClustering(**kwargs)
    else:
        raise ValueError(f"Unknown method: {method}")
    
    labels = clusterer.fit_predict(X)
    return labels


def find_local_maxima(gdf: gpd.GeoDataFrame, labels: np.ndarray, dims: str = '3d') -> gpd.GeoDataFrame:
    """Find local maxima for each cluster.

    For each cluster, identifies the point with the highest elevation.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Input GeoDataFrame.
    labels : np.ndarray
        Cluster labels.
    dims : str
        Dimensionality used for clustering.

    Returns
    -------
    gpd.GeoDataFrame
        GeoDataFrame with local maxima points.
    """
    maxima = []
    for label in np.unique(labels):
        if label == -1:  # Skip noise
            continue
        cluster = gdf[labels == label]
        max_idx = cluster['elev_m'].idxmax()
        maxima.append(cluster.loc[max_idx])
    
    return gpd.GeoDataFrame(maxima, crs=gdf.crs)


def write_clustered_csv(gdf: gpd.GeoDataFrame, labels: np.ndarray, output_path: Path) -> None:
    """Write clustered data to CSV.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Input GeoDataFrame.
    labels : np.ndarray
        Cluster labels.
    output_path : Path
        Output CSV path.
    """
    gdf = gdf.copy()
    gdf['cluster_label'] = labels
    gdf.to_csv(output_path, index=False)


def write_geopackage(
    gdf: gpd.GeoDataFrame, 
    labels: np.ndarray, 
    maxima_gdf: gpd.GeoDataFrame, 
    output_path: Path, 
    overwrite: bool = True
) -> None:
    """Write clustered data and maxima to GeoPackage.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Clustered points.
    labels : np.ndarray
        Cluster labels.
    maxima_gdf : gpd.GeoDataFrame
        Local maxima points.
    output_path : Path
        Output GPKG path.
    overwrite : bool
        Whether to overwrite existing file.
    """
    gdf = gdf.copy()
    gdf['cluster_label'] = labels
    
    # Write points layer
    gdf.to_file(output_path, layer='clustered_points', driver='GPKG')
    
    # Write maxima layer if any
    if not maxima_gdf.empty:
        maxima_gdf.to_file(output_path, layer='local_maxima', driver='GPKG')


def plot_clusters_2d(gdf: gpd.GeoDataFrame, labels: np.ndarray, output_path: Path) -> None:
    """Create 2D scatter plot of clusters.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Input GeoDataFrame.
    labels : np.ndarray
        Cluster labels.
    output_path : Path
        Output plot path.
    """
    fig, ax = plt.subplots(figsize=(10, 8))
    unique_labels = np.unique(labels)
    
    for label in unique_labels:
        if label == -1:
            color = 'black'
            label_name = 'Noise'
        else:
            color = plt.cm.viridis(label / max(1, len(unique_labels) - 1))
            label_name = f'Cluster {label}'
        
        cluster = gdf[labels == label]
        ax.scatter(cluster.x, cluster.y, c=[color], label=label_name, alpha=0.7)
    
    ax.set_xlabel('X (meters)')
    ax.set_ylabel('Y (meters)')
    ax.set_title('Cluster Analysis Results')
    ax.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()


def plot_elevation_profile(gdf: gpd.GeoDataFrame, labels: np.ndarray, output_path: Path) -> None:
    """Create elevation profile plot colored by cluster.

    Parameters
    ----------
    gdf : gpd.GeoDataFrame
        Input GeoDataFrame.
    labels : np.ndarray
        Cluster labels.
    output_path : Path
        Output plot path.
    """
    fig, ax = plt.subplots(figsize=(12, 6))
    unique_labels = np.unique(labels)
    
    for label in unique_labels:
        if label == -1:
            continue  # Skip noise for profile
        
        cluster = gdf[labels == label]
        color = plt.cm.viridis(label / max(1, len(unique_labels) - 1))
        ax.scatter(cluster.x, cluster.elev_m, c=[color], label=f'Cluster {label}', alpha=0.7)
    
    ax.set_xlabel('X (meters)')
    ax.set_ylabel('Elevation (m)')
    ax.set_title('Elevation Profile by Cluster')
    ax.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()