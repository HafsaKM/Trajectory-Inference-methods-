import glob
import umap
import os
import pickle
import re
import shlex
import shutil
import warnings
from collections import Counter
from copy import deepcopy
from subprocess import PIPE, Popen
from typing import Tuple, Iterable, Dict, Optional, List
from sklearn.neighbors import NearestNeighbors  
from typing import Optional, List, Tuple, Iterable
from sklearn.manifold import SpectralEmbedding
import seaborn as sns

import bhtsne
import fcsparser
import matplotlib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import scipy
import sklearn
from numpy.core.umath_tests import inner1d

from .core import wishbone as c_wishbone

try:
    import phenograph
except ImportError:
    raise ImportError(
        "\nplease install Phenograph:\n\n\thttps://github.com/dpeerlab/PhenoGraph"
    )

# set plotting defaults
with warnings.catch_warnings():
    warnings.simplefilter("ignore")  # catch experimental ipython widget warning
    import seaborn as sns

    sns.set(context="paper", style="ticks", font_scale=1.5, font="Bitstream Vera Sans")

cmap = matplotlib.cm.Spectral_r
size = 8


def qualitative_colors(n):
    """\
    Generalte list of colors

    :param n:
        Number of colors
    """
    return sns.color_palette("Set1", n)


def get_fig(
    fig: matplotlib.figure.Figure = None,
    ax: matplotlib.axes.Axes = None,
    figsize=[6.5, 6.5],
) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
    """\
    fills in any missing axis or figure with the currently active one

    :param fig:
        matplotlib Figure object
    :param ax:
        matplotlib Axis object
    :param figsize:
        matplotlib Figure size
    """
    if not fig:
        fig = plt.figure(figsize=figsize)
    if not ax:
        ax = plt.gca()
    return fig, ax


def density_2d(x, y):
    """\
    return x and y and their density z, sorted by their density (smallest to largest)

    :param x:
    :param y:
    """
    xy = np.vstack([np.ravel(x), np.ravel(y)])
    z = scipy.stats.gaussian_kde(xy)(xy)
    i = np.argsort(z)
    return np.ravel(x)[i], np.ravel(y)[i], np.arcsinh(z[i])


class SCData(object):
    def __init__(
        self, data, data_type: str = "sc-seq", metadata: Optional[pd.DataFrame] = None
    ):
        """\
        Container class for single cell data

        :param data:
            DataFrame of cells X genes representing expression
        :param data_type:
            Type of the data: Can be either 'sc-seq' or 'masscyt'
        :param metadata:
            None or DataFrame representing metadata about the cells
        """
        if not (isinstance(data, pd.DataFrame)):
            raise TypeError("data must be of type or DataFrame")
        if not data_type in ["sc-seq", "masscyt"]:
            raise RuntimeError("data_type must be either sc-seq or masscyt")
        if metadata is None:
            metadata = pd.DataFrame(index=data.index, dtype="O")
        self._data = data
        self._metadata = metadata
        self._data_type = data_type
        self._normalized = False
        self._pca = None
        self._tsne = None
        self._diffusion_eigenvectors = None
        self._diffusion_eigenvalues = None
        self._diffusion_map_correlations = None
        self._normalized = False
        self._cluster_assignments = None
        self._latent_space = None
        self._umap = None

        # Library size
        self._library_sizes = None
    
    @property
    def latent_space(self):
        return self._latent_space
    
    @latent_space.setter
    def latent_space(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError("latent_space must be a pd.DataFrame object")
        self._latent_space = item
    
    @property
    def umap(self):
        return self._umap

    @umap.setter
    def umap(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError("umap must be a pd.DataFrame object")
        self._umap = item

    def load_latent_space(self, latent_space_file):
        self.latent_space = pd.read_csv(latent_space_file, index_col=0)

    def run_umap(self, n_neighbors: int = 15, min_dist: float = 0.5, n_components: int = 2, use_rep: str = 'latent'):
        """Run UMAP on the specified representation (latent space or PCA)."""
        if self.latent_space is None:
            raise RuntimeError("Please load latent space embedding before running UMAP.")

        if use_rep == 'latent':
            data_to_use = self.latent_space
        else:
            raise RuntimeError("Only 'latent' representation is supported.")

        reducer = umap.UMAP(n_neighbors=n_neighbors, min_dist=min_dist, n_components=n_components, random_state=42)
        self.umap = pd.DataFrame(reducer.fit_transform(data_to_use), index=self.data.index, columns=['x', 'y'])
        
# custom till here

    def save(self, fout: str) -> None:
        """\
        :param fout:
            name of archive to store pickled SCData data in. Should end in '.p'.
        :return:
            `None`
        """
        with open(fout, "wb") as f:
            pickle.dump(vars(self), f)

    def save_as_wishbone(self, fout: str) -> None:
        """\

        :param fout:
            name of archive to store pickled Wishbone data in. Should end in '.p'

        :return:
            `None`
        """
        wb = Wishbone(self, True)
        wb.save(fout)

    @classmethod
    def load(cls, fin: str):
        """\

        :param fin:
            name of pickled archive containing SCData data

        :return:
            SCData
        """
        with open(fin, "rb") as f:
            data = pickle.load(f)
        scdata = cls(data["_data"], data["_data_type"], data["_metadata"])
        del data["_data"]
        del data["_data_type"]
        del data["_metadata"]
        for k, v in data.items():
            setattr(scdata, k[1:], v)
        return scdata

    def __repr__(self):
        c, g = self.data.shape
        _repr = "SCData: {c} cells x {g} genes\n".format(g=g, c=c)
        for k, v in sorted(vars(self).items()):
            if not (k == "_data"):
                _repr += "\n{}={}".format(k[1:], "None" if v is None else "True")
        return _repr

    @property
    def data_type(self):
        return self._data_type

    @property
    def data(self):
        return self._data

    @data.setter
    def data(self, item):
        if not (isinstance(item, pd.DataFrame)):
            raise TypeError("SCData.data must be of type DataFrame")
        self._data = item

    @property
    def metadata(self):
        return self._metadata

    @metadata.setter
    def metadata(self, item):
        if not isinstance(item, pd.DataFrame):
            raise TypeError("SCData.metadata must be of type DataFrame")
        self._metadata = item

    @property
    def pca(self):
        return self._pca

    @pca.setter
    def pca(self, item):
        if not (isinstance(item, dict) or item is None):
            raise TypeError("self.pca must be a dictionary of pd.DataFrame object")
        self._pca = item

    @property
    def tsne(self):
        return self._tsne

    @tsne.setter
    def tsne(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError("self.tsne must be a pd.DataFrame object")
        self._tsne = item

    @property
    def diffusion_eigenvectors(self):
        return self._diffusion_eigenvectors

    @diffusion_eigenvectors.setter
    def diffusion_eigenvectors(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError("self.diffusion_eigenvectors must be a pd.DataFrame object")
        self._diffusion_eigenvectors = item

    @property
    def diffusion_eigenvalues(self):
        return self._diffusion_eigenvalues

    @diffusion_eigenvalues.setter
    def diffusion_eigenvalues(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError("self.diffusion_eigenvalues must be a pd.DataFrame object")
        self._diffusion_eigenvalues = item

    @property
    def diffusion_map_correlations(self):
        return self._diffusion_map_correlations

    @diffusion_map_correlations.setter
    def diffusion_map_correlations(self, item):
        if not (isinstance(item, pd.DataFrame) or item is None):
            raise TypeError(
                "self.diffusion_map_correlations must be a pd.DataFrame" "object"
            )
        self._diffusion_map_correlations = item

    @property
    def library_sizes(self):
        return self._library_sizes

    @library_sizes.setter
    def library_sizes(self, item):
        if not (isinstance(item, pd.Series) or item is None):
            raise TypeError("self.library_sizes must be a pd.Series object")

    @property
    def cluster_assignments(self):
        return self._cluster_assignments

    @cluster_assignments.setter
    def cluster_assignments(self, item):
        if not (isinstance(item, pd.Series) or item is None):
            raise TypeError("self.cluster_assignments must be a pd.Series " "object")
        self._cluster_assignments = item
    
    @classmethod
    def from_csv(cls, counts_csv_file, data_type="sc-seq", cell_axis=0, normalize=True):
        """\
        Load data from a csv file

        :param counts_csv_file:
            Path to file containing counts table.
        :param data_type:
            Type of data. Either 'sc-seq' or 'masscyt'
        :param cell_axis:
            Axis along which the cells are represented. (0 = rows, 1 = columns)
        :param normalize:
            If True, normalize the data

        :return:
            SCData object
        """
        # Load data
        df = pd.read_csv(counts_csv_file, index_col=0)
        if cell_axis == 1:
            df = df.transpose()

        # Construct class object
        scdata = cls(df, data_type=data_type)

        # Normalize if specified
        if data_type == "sc-seq" and normalize:
            scdata.normalize()

        return scdata

    def normalize(self):
        """\
        Normalize the data
        """
        if self._data_type == "sc-seq":
            self._data = self._data.div(self._data.sum(axis=1), axis=0) * 1e4
            self._data = np.log1p(self._data)
            self._normalized = True

    @classmethod
    def from_fcs(
        cls,
        fcs_file,
        cofactor: int = 5,
        metadata_channels: List[str] = [
            "Time",
            "Event_length",
            "DNA1",
            "DNA2",
            "Cisplatin",
            "beadDist",
            "bead1",
        ],
    ):

        # Parse the fcs file
        text, data = fcsparser.parse(fcs_file)
        data = data.astype(np.float64)

        # Extract the S and N features (Indexing assumed to start from 1)
        # Assumes channel names are in S
        no_channels = text["$PAR"]
        channel_names = [""] * no_channels
        for i in range(1, no_channels + 1):
            # S name
            try:
                channel_names[i - 1] = text["$P%dS" % i]
            except KeyError:
                channel_names[i - 1] = text["$P%dN" % i]
        data.columns = channel_names

        # Metadata and data
        metadata_channels = data.columns.intersection(metadata_channels)
        data_channels = data.columns.difference(metadata_channels)
        metadata = data[metadata_channels]
        data = data[data_channels]

        # Transform if necessary
        if cofactor is not None and cofactor > 0:
            data = np.arcsinh(np.divide(data, cofactor))

        # Create and return scdata object
        scdata = cls(data, "masscyt", metadata)
        return scdata

    def normalize_scseq_data(self):
        """\
        Normalize single cell RNA-seq data: Divide each cell by its molecule count
        and multiply counts of cells by the median of the molecule counts

        :return:
            SCData
        """

        molecule_counts = self.data.sum(axis=1)
        data = self.data.div(molecule_counts, axis=0).mul(
            np.median(molecule_counts), axis=0
        )
        scdata = SCData(data=data, metadata=self.metadata)
        scdata._normalized = True

        # check that none of the genes are empty; if so remove them
        nonzero_genes = scdata.data.sum(axis=0) != 0
        scdata.data = scdata.data.loc[:, nonzero_genes].astype(np.float32)

        # set unnormalized_cell_sums
        self.library_sizes = molecule_counts
        scdata._library_sizes = molecule_counts

        return scdata

    def run_pca(self, n_components: int = 100):
        """\
        Principal component analysis of the data.

        :param n_components:
            Number of components to project the data
        """

        X = self.data.values
        # Make sure data is zero mean
        X = np.subtract(X, np.amin(X))
        X = np.divide(X, np.amax(X))

        # Compute covariance matrix
        if X.shape[1] < X.shape[0]:
            C = np.cov(X, rowvar=0)
        # if N>D, we better use this matrix for the eigendecomposition
        else:
            C = np.multiply((1 / X.shape[0]), np.dot(X, X.T))

        # Perform eigendecomposition of C
        C[np.where(np.isnan(C))] = 0
        C[np.where(np.isinf(C))] = 0
        l, M = np.linalg.eig(C)

        # Sort eigenvectors in descending order
        ind = np.argsort(l)[::-1]
        l = l[ind]
        if n_components < 1:
            n_components = (
                np.where(np.cumsum(np.divide(l, np.sum(l)), axis=0) >= n_components)[0][
                    0
                ]
                + 1
            )
            print("Embedding into " + str(n_components) + " dimensions.")
        if n_components > M.shape[1]:
            n_components = M.shape[1]
            print("Target dimensionality reduced to " + str(n_components) + ".")

        M = M[:, ind[:n_components]]
        l = l[:n_components]

        # Apply mapping on the data
        if X.shape[1] >= X.shape[0]:
            M = np.multiply(np.dot(X.T, M), (1 / np.sqrt(X.shape[0] * l)).T)

        loadings = pd.DataFrame(data=M, index=self.data.columns)
        l = pd.DataFrame(l)

        self.pca = {"loadings": loadings, "eigenvalues": l}

    def plot_pca_variance_explained(
        self,
        n_components: int = 30,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
        ylim: Tuple[float, float] = (0, 0.1),
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot the variance explained by different principal components

        :param n_components:
            Number of components to show the variance
        :param ylim:
            y-axis limits
        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object

        :return:
            fig, ax
        """
        if self.pca is None:
            raise RuntimeError("Please run run_pca() before plotting")

        fig, ax = get_fig(fig=fig, ax=ax)
        ax.plot(np.ravel(self.pca["eigenvalues"].values))
        plt.ylim(ylim)
        plt.xlim((0, n_components))
        plt.xlabel("Components")
        plt.ylabel("Variance explained")
        plt.title("Principal components")
        sns.despine(ax=ax)
        return fig, ax

    def run_tsne(self, n_components: int = 2, perplexity: int = 30, rand_seed: int = -1, use_latent_space: bool = False) -> None:
        """Run tSNE on the data."""
        if use_latent_space:
            data = self.latent_space
        else:
            data = self.data

        if self.data_type == "sc-seq" and not use_latent_space:
            if self.pca is None:
                raise RuntimeError(
                    "Please run PCA using run_pca before running tSNE for single cell RNA-seq"
                )
            data = self.pca["loadings"]

        # Reduce perplexity if necessary
        data = data.astype(np.float64)
        perplexity_limit = 15
        if data.shape[0] < 100 and perplexity > perplexity_limit:
            print(
                f"Reducing perplexity to {perplexity_limit} since there are <100 cells in the dataset."
            )
            perplexity = perplexity_limit
        self.tsne = pd.DataFrame(
            bhtsne.tsne(data, perplexity=perplexity, rand_seed=rand_seed),
            index=self.data.index,
            columns=["x", "y"],
        )

    def plot_tsne(
        self,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
        title: str = "tSNE projection",
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot tSNE projections of the data

        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object
        :param title:
            Title for the plot

        :return:
            fig, ax
        """
        if self.tsne is None:
            raise RuntimeError("Please run tSNE using run_tsne before plotting ")
        fig, ax = get_fig(fig=fig, ax=ax)
        plt.scatter(
            self.tsne["x"], self.tsne["y"], s=size, color=qualitative_colors(2)[1]
        )
        ax.xaxis.set_major_locator(plt.NullLocator())
        ax.yaxis.set_major_locator(plt.NullLocator())
        ax.set_title(title)
        return fig, ax

    def plot_tsne_by_cell_sizes(
        self, fig: matplotlib.figure.Figure = None, ax: matplotlib.axes.Axes = None
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot tSNE projections of the data with cells colored by molecule counts

        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object

        :return:
            fig, ax
        """
        if self.data_type == "masscyt":
            raise RuntimeError(
                "plot_tsne_by_cell_sizes is not applicable \n\
                for mass cytometry data. "
            )

        fig, ax = get_fig(fig, ax)
        if self.tsne is None:
            raise RuntimeError("Please run run_tsne() before plotting.")
        if self._normalized:
            sizes = self.library_sizes
        else:
            sizes = self.data.sum(axis=1)
        plt.scatter(self.tsne["x"], self.tsne["y"], s=size, c=sizes, cmap=cmap)
        ax.xaxis.set_major_locator(plt.NullLocator())
        ax.yaxis.set_major_locator(plt.NullLocator())
        plt.colorbar()
        return fig, ax

    def run_phenograph(self, n_pca_components: int = 15, **kwargs):
        """\
        Identify clusters in the data using phenograph. Phenograph is run on the
        principal component projections for single cell RNA-seq data and on the
        expression matrix for mass cytometry data

        :param n_pca_components:
            Number of components to use for running tSNE for single cell RNA-seq data.
            Ignored for mass cytometry
        :param kwargs:
            Optional arguments to `~phenograph`

        :return:
            `None`
        """

        data = deepcopy(self.data)
        if self.data_type == "sc-seq":
            data -= np.min(np.ravel(data))
            data /= np.max(np.ravel(data))
            data = pd.DataFrame(
                np.dot(data, self.pca["loadings"].iloc[:, 0:n_pca_components]),
                index=self.data.index,
            )

        communities, graph, Q = phenograph.cluster(data, **kwargs)
        self.cluster_assignments = pd.Series(communities, index=data.index)

    def plot_phenograph_clusters(
        self,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
        labels: dict = None,
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot phenograph clustes on the tSNE map

        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object
        :param labels:
            Dictionary of labels for each cluster

        :return:
            fig, ax
        """

        if self.tsne is None:
            raise RuntimeError("Please run tSNE before plotting phenograph clusters.")

        fig, ax = get_fig(fig=fig, ax=ax)
        clusters = sorted(set(self.cluster_assignments))
        colors = qualitative_colors(len(clusters))
        for i in range(len(clusters)):
            if labels:
                label = labels[i]
            else:
                label = clusters[i]
            data = self.tsne.loc[self.cluster_assignments == clusters[i], :]
            ax.plot(
                data["x"],
                data["y"],
                c=colors[i],
                linewidth=0,
                marker="o",
                markersize=np.sqrt(size),
                label=label,
            )
        ax.legend(loc="center left", bbox_to_anchor=(1, 0.5), markerscale=3)
        ax.xaxis.set_major_locator(plt.NullLocator())
        ax.yaxis.set_major_locator(plt.NullLocator())
        return fig, ax

    def summarize_phenograph_clusters(
        self, fig: matplotlib.figure.Figure = None, ax: matplotlib.axes.Axes = None
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Average expression of genes in phenograph clusters

        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object

        :return:
            fig, ax
        """
        if self.cluster_assignments is None:
            raise RuntimeError(
                "Please run phenograph before deriving summary of gene expression."
            )

        # Calculate the means
        means = self.data.groupby(self.cluster_assignments).apply(lambda x: np.mean(x))

        # Calculate percentages
        counter = Counter(self.cluster_assignments)
        means.index = [
            "%d (%.2f%%)" % (i, counter[i] / self.data.shape[0] * 100)
            for i in means.index
        ]

        # Plot
        fig, ax = get_fig(fig, ax, [8, 5])
        sns.heatmap(means)
        plt.ylabel("Phenograph Clusters")
        plt.xlabel("Markers")

        return fig, ax

    def select_clusters(self, clusters: list):
        """\
        Subselect cells from specific `~phenograph` clusters

        :param clusters:
            List of phenograph clusters to select

        :return:
            scdata
        """
        if self.cluster_assignments is None:
            raise RuntimeError("Please run phenograph before subselecting cells.")
        if len(set(clusters).difference(self.cluster_assignments)) > 0:
            raise RuntimeError(
                "Some of the clusters specified are not present. Please select a "
                "subset of phenograph clusters"
            )

        # Subset of cells to use
        cells = self.data.index[self.cluster_assignments.isin(clusters)]

        # Create new SCData object
        data = self.data.loc[cells]
        if self.metadata is not None:
            meta = self.metadata.loc[cells]
        scdata = SCData(data, self.data_type, meta)
        return scdata

    def run_diffusion_map(self, n_components: int = 10):
        """Run diffusion map on the latent space."""
        from sklearn.manifold import SpectralEmbedding
        if self.latent_space is None:
            raise RuntimeError("Please load latent space embedding before running diffusion maps.")
        embedder = SpectralEmbedding(n_components=n_components)
        self.diffusion_eigenvectors = pd.DataFrame(embedder.fit_transform(self.latent_space), index=self.data.index)
        self.diffusion_eigenvalues = embedder.eigenvalues_

    def plot_diffusion_components(self, use_umap: bool = True) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plots the diffusion components on UMAP or tSNE maps

        :param use_umap:
            If True, plots on UMAP coordinates, otherwise on tSNE coordinates.

        :return:
            fig, ax
        """
        if use_umap:
            if self.umap is None:
                raise RuntimeError("Please run UMAP before plotting diffusion components.")
            x, y = self.umap["x"], self.umap["y"]
            title_suffix = "UMAP"
        else:
            if self.tsne is None:
                raise RuntimeError("Please run tSNE before plotting diffusion components.")
            x, y = self.tsne["x"], self.tsne["y"]
            title_suffix = "tSNE"

        if self.diffusion_eigenvectors is None:
            raise RuntimeError(
                "Please run diffusion maps using run_diffusion_map before plotting"
            )

        height = int(2 * np.ceil(self.diffusion_eigenvalues.shape[0] / 5))
        width = 10
        fig = plt.figure(figsize=[width, height])
        n_rows = int(height / 2)
        n_cols = int(width / 2)
        gs = plt.GridSpec(n_rows, n_cols)

        for i in range(self.diffusion_eigenvectors.shape[1]):
            ax = plt.subplot(gs[i // n_cols, i % n_cols])
            plt.scatter(
                x,
                y,
                c=self.diffusion_eigenvectors.iloc[:, i],
                cmap='viridis',  # You can change the colormap as needed
                edgecolors="none",
                s=10,  # Adjust the size as needed
            )
            ax.xaxis.set_major_locator(plt.NullLocator())
            ax.yaxis.set_major_locator(plt.NullLocator())
            ax.set_aspect("equal")
            plt.title(f"Component {i} ({title_suffix})", fontsize=10)

        return fig, ax

   

    def plot_diffusion_eigen_vectors(
        self,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
        title="Diffusion eigen vectors",
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plots the eigen values associated with diffusion components

        :return:
            fig, ax
        """
        if self.diffusion_eigenvectors is None:
            raise RuntimeError(
                "Please run diffusion maps using run_diffusion_map before plotting"
            )

        fig, ax = get_fig(fig=fig, ax=ax)
        ax.plot(np.ravel(self.diffusion_eigenvalues.values))
        plt.scatter(
            range(len(self.diffusion_eigenvalues)),
            self._diffusion_eigenvalues,
            s=20,
            edgecolors="none",
            color="red",
        )
        plt.xlabel("Diffusion components")
        plt.ylabel("Eigen values")
        plt.title(title)
        plt.xlim([-0.1, len(self.diffusion_eigenvalues) - 0.9])
        sns.despine(ax=ax)
        return fig, ax

    @staticmethod
    def _correlation(x: np.array, vals: np.array):
        x = x[:, np.newaxis]
        mu_x = x.mean()  # cells
        mu_vals = vals.mean(axis=0)  # cells by gene --> cells by genes
        sigma_x = x.std()
        sigma_vals = vals.std(axis=0)

        return ((vals * x).mean(axis=0) - mu_vals * mu_x) / (sigma_vals * sigma_x)

    def run_diffusion_map_correlations(
        self, components: list = None, no_cells: int = 10
    ) -> None:
        """\
        Determine gene expression correlations along diffusion components

        :param components:
            List of components to generate the correlations. All the components are
            used by default.
        :param no_cells:
            Window size for smoothing

        :return:
            `None`
        """
        if self.data_type == "masscyt":
            raise RuntimeError(
                "This function is designed to work for single cell RNA-seq"
            )
        if self.diffusion_eigenvectors is None:
            raise RuntimeError(
                "Please run diffusion maps using run_diffusion_map before determining "
                "correlations"
            )

        if components is None:
            components = np.arange(self.diffusion_eigenvectors.shape[1])
        else:
            components = np.array(components)
        components = components[components != 0]

        # Container
        diffusion_map_correlations = np.empty(
            (self.data.shape[1], self.diffusion_eigenvectors.shape[1]), dtype=np.float
        )
        for component_index in components:
            component_data = self.diffusion_eigenvectors.loc[:, component_index]

            order = self.data.index[np.argsort(component_data)]
            x = component_data[order].rolling(no_cells).mean()[no_cells:]
            # x = pd.rolling_mean(component_data[order], no_cells)[no_cells:]

            # this fancy indexing will copy self.data
            vals = self.data.loc[order, :].rolling(no_cells).mean()[no_cells:].values
            # vals = pd.rolling_mean(self.data.loc[order, :], no_cells, axis=0)[
            # no_cells:]
            cor_res = self._correlation(x, vals)
            # assert cor_res.shape == (gene_shape,)
            diffusion_map_correlations[:, component_index] = self._correlation(x, vals)

        # this is sorted by order, need it in original order (reverse the sort)
        self.diffusion_map_correlations = pd.DataFrame(
            diffusion_map_correlations[:, components],
            index=self.data.columns,
            columns=components,
        )

    def plot_gene_component_correlations(
        self,
        components: Iterable[int] = None,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
        title: str = "Gene vs. Diffusion Component Correlations",
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        plots gene-component correlations for a subset of components

        :param components:
            Integer component numbers
        :param fig:
            Figure
        :param ax:
            Axis
        :param title:
            title for the plot

        :return:
            fig, ax
        """
        fig, ax = get_fig(fig=fig, ax=ax)
        if self.diffusion_map_correlations is None:
            raise RuntimeError(
                "Please run determine_gene_diffusion_correlations() "
                "before attempting to visualize the correlations."
            )

        if components is None:
            components = self.diffusion_map_correlations.columns
        colors = qualitative_colors(len(components))

        for c, color in zip(components, colors):
            with warnings.catch_warnings():
                warnings.simplefilter(
                    "ignore"
                )  # catch experimental ipython widget warning
                sns.kdeplot(
                    self.diffusion_map_correlations[c].fillna(0),
                    label=c,
                    ax=ax,
                    color=color,
                )
        sns.despine(ax=ax)
        ax.set_title(title)
        ax.set_xlabel("correlation")
        ax.set_ylabel("gene density")
        plt.legend()
        return fig, ax

    @staticmethod
    def _gmt_options():
        mouse_options = os.listdir(os.path.expanduser("~/.wishbone/tools/mouse"))
        human_options = os.listdir(os.path.expanduser("~/.wishbone/tools/human"))
        print(
            "Available GSEA .gmt files:\n\nmouse:\n{m}\n\nhuman:\n{h}\n".format(
                m="\n".join(mouse_options), h="\n".join(human_options)
            )
        )
        print("Please specify the gmt_file parameter as gmt_file=(organism, filename)")

    @staticmethod
    def _gsea_process(c, diffusion_map_correlations, output_stem, gmt_file):

        # save the .rnk file
        out_dir, out_prefix = os.path.split(output_stem)
        genes_file = "{stem}_cmpnt_{component}.rnk".format(
            stem=output_stem, component=c
        )
        ranked_genes = diffusion_map_correlations.loc[:, c].sort_values(
            inplace=False, ascending=False
        )

        # set any NaN to 0
        ranked_genes = ranked_genes.fillna(0)

        # dump to file
        pd.DataFrame(ranked_genes).to_csv(genes_file, sep="\t", header=False)

        # Construct the GSEA call
        cmd = shlex.split(
            "java -cp {user}/.wishbone/tools/gsea2-2.2.1.jar -Xmx1g "
            "xtools.gsea.GseaPreranked -collapse false -mode Max_probe -norm meandiv "
            "-nperm 1000 -include_only_symbols true -make_sets true -plot_top_x 0 "
            "-set_max 500 -set_min 50 -zip_report false -gui false -rnk {rnk} "
            "-rpt_label {out_prefix}_{component} -out {out_dir}/ -gmx {gmt_file}"
            "".format(
                user=os.path.expanduser("~"),
                rnk=genes_file,
                out_prefix=out_prefix,
                component=c,
                out_dir=out_dir,
                gmt_file=gmt_file,
            )
        )

        # Call GSEA
        p = Popen(cmd, stderr=PIPE)
        _, err = p.communicate()

        # remove annoying suffix from GSEA
        if err:
            return err
        else:
            pattern = out_prefix + "_" + str(c) + ".GseaPreranked.[0-9]*"
            repl = out_prefix + "_" + str(c)
            files = os.listdir(out_dir)
            for f in files:
                mo = re.match(pattern, f)
                if mo:
                    curr_name = mo.group(0)
                    shutil.move(
                        "{}/{}".format(out_dir, curr_name),
                        "{}/{}".format(out_dir, repl),
                    )
                    return err

            # execute if file cannot be found
            return b"GSEA output pattern was not found, and could not be changed."

    def run_gsea(
        self,
        output_stem: str,
        gmt_file: str = None,
        components: Iterable[int] = None,
        enrichment_threshold: float = 1e-1,
    ) -> Dict:
        """\
        Run GSEA using gene rankings from diffusion map correlations

        :param output_stem:
            the file location and prefix for the output of GSEA
        :param gmt_file:
            GMT file containing the gene sets. Use None to see a list of options
        :param components:
            Iterable of integer component numbers
        :param enrichment_threshold:
            FDR corrected p-value significance threshold for gene set enrichments

        :return:
            Dictionary containing the top enrichments for each component
        """

        out_dir, out_prefix = os.path.split(output_stem)
        out_dir += "/"
        os.makedirs(out_dir, exist_ok=True)

        if self.diffusion_eigenvectors is None:
            raise RuntimeError(
                "Please run run_diffusion_map_correlations() "
                "before running GSEA to annotate those components."
            )

        if not gmt_file:
            self._gmt_options()
            return
        else:
            if not len(gmt_file) == 2:
                raise ValueError("gmt_file should be a tuple of (organism, filename).")
            gmt_file = os.path.expanduser("~/.wishbone/tools/{}/{}").format(*gmt_file)

        if components is None:
            components = self.diffusion_map_correlations.columns

        # Run GSEA
        print(
            "If running in notebook, please look at the command line window for GSEA "
            "progress log"
        )
        reports = dict()
        for c in components:
            res = self._gsea_process(
                c, self._diffusion_map_correlations, output_stem, gmt_file
            )
            # Load results
            if res == b"":
                # Positive correlations
                df = pd.read_csv(
                    glob.glob(output_stem + "_%d/gsea*pos*xls" % c)[0], sep="\t"
                )
                reports[c] = dict()
                reports[c]["pos"] = df["FDR q-val"][0:5]
                reports[c]["pos"] = reports[c]["pos"][
                    reports[c]["pos"] < enrichment_threshold
                ]

                # Negative correlations
                df = pd.read_csv(
                    glob.glob(output_stem + "_%d/gsea*neg*xls" % c)[0], sep="\t"
                )
                reports[c]["neg"] = df["FDR q-val"][0:5]
                reports[c]["neg"] = reports[c]["neg"][
                    reports[c]["neg"] < enrichment_threshold
                ]

        # Return results
        return reports

    # todo add option to plot phenograph cluster that these are being DE in.
    def plot_gene_expression(
        self, genes: Iterable[str]
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot gene expression on tSNE maps

        :param genes:
            Iterable of strings to plot on tSNE

        :return:
            fig, axes
        """
        not_in_dataframe = set(genes).difference(self.data.columns)
        if not_in_dataframe:
            if len(not_in_dataframe) < len(genes):
                print(
                    "The following genes were either not observed in the experiment, "
                    "or the wrong gene symbol was used: {!r}".format(not_in_dataframe)
                )
            else:
                print(
                    "None of the listed genes were observed in the experiment, or the "
                    "wrong symbols were used."
                )
                return

        # remove genes missing from experiment
        genes = set(genes).difference(not_in_dataframe)

        height = int(2 * np.ceil(len(genes) / 5))
        width = 10
        fig = plt.figure(figsize=[width, height + 0.25])
        n_rows = int(height / 2)
        n_cols = int(width / 2)
        gs = plt.GridSpec(n_rows, n_cols)

        axes = []
        for i, g in enumerate(genes):
            ax = plt.subplot(gs[i // n_cols, i % n_cols])
            axes.append(ax)
            if self.data_type == "sc-seq":
                plt.scatter(
                    self.tsne["x"],
                    self.tsne["y"],
                    c=np.arcsinh(self.data[g]),
                    cmap=cmap,
                    edgecolors="none",
                    s=size,
                )
            else:
                plt.scatter(
                    self.tsne["x"],
                    self.tsne["y"],
                    c=self.data[g],
                    cmap=cmap,
                    edgecolors="none",
                    s=size,
                )
            ax.set_title(g)
            ax.xaxis.set_major_locator(plt.NullLocator())
            ax.yaxis.set_major_locator(plt.NullLocator())

        return fig, axes



class Wishbone:
    def __init__(self, scdata, ignore_dm_check: bool = False):
        """\
        Container class for Wishbone

        :param data:
            SCData object
        """
        if not ignore_dm_check and scdata.diffusion_eigenvectors is None and scdata.latent_space is None:
            raise RuntimeError("Please use scdata with diffusion maps or latent space embedding run for Wishbone")

        self._scdata = scdata
        self._trajectory = None
        self._branch = None
        self._waypoints = None
        self._branch_colors = None

    def __repr__(self):
        c, g = self.scdata.data.shape
        _repr = "Wishbone object: {c} cells x {g} genes\n".format(g=g, c=c)
        for k, v in sorted(vars(self).items()):
            if not (k == "_scdata"):
                _repr += "\n{}={}".format(k[1:], "None" if v is None else "True")
        return _repr

    def save(self, fout: str):
        """\

        :param fout:
            name of archive to store pickled Experiment data in. Should end in '.p'

        :return:
            `None`
        """
        with open(fout, "wb") as f:
            pickle.dump(vars(self), f)

    @classmethod
    def load(cls, fin: str):
        """\

        :param fin:
            name of pickled archive containing Experiment data

        :return:
            Experiment
        """
        with open(fin, "rb") as f:
            data = pickle.load(f)
        wb = cls(data["_scdata"], True)
        del data["_scdata"]
        for k, v in data.items():
            setattr(wb, k[1:], v)
        return wb

    @property
    def scdata(self):
        return self._scdata

    @scdata.setter
    def scdata(self, item):
        if not (isinstance(item, SCData)):
            raise TypeError("data must be of type wishbone.wb.SCData")
        self._scdata = item

    @property
    def branch(self):
        return self._branch

    @branch.setter
    def branch(self, item):
        if not (isinstance(item, pd.Series) or item is None):
            raise TypeError("self.branch must be a pd.Series object")
        self._branch = item

    @property
    def trajectory(self):
        return self._trajectory

    @trajectory.setter
    def trajectory(self, item):
        if not (isinstance(item, pd.Series) or item is None):
            raise TypeError("self.trajectory must be a pd.Series object")
        self._trajectory = item

    @property
    def waypoints(self):
        return self._waypoints

    @waypoints.setter
    def waypoints(self, item):
        if not (isinstance(item, list) or item is None):
            raise TypeError("self.waypoints must be a list object")
        self._waypoints = item

    @property
    def branch_colors(self):
        return self._branch_colors

    @branch_colors.setter
    def branch_colors(self, item):
        if not (isinstance(item, dict) or item is None):
            raise TypeError("self.branch_colors a pd.Series object")
        self._branch_colors = item

    def run_wishbone(self, start_cell, branch=True, k=15, components_list=[1, 2, 3], num_waypoints=250):
        if self.scdata.latent_space is None:
            raise RuntimeError("Please load latent space embedding before running Wishbone.")
        
        # self.scdata.run_umap()  # Optionally run UMAP if needed for visualization

        # Use latent space directly for Wishbone calculations
        latent_data = self.scdata.latent_space
        nbrs = NearestNeighbors(n_neighbors=k + 1, metric='euclidean').fit(latent_data)
        knn_graph = nbrs.kneighbors_graph(latent_data, mode='distance')

        # Start cell index
        s = np.where(self.scdata.latent_space.index == start_cell)[0]
        if len(s) == 0:
            raise RuntimeError(
                "Start cell %s not found in data. Please rerun with correct start "
                "cell" % start_cell
            )
        if isinstance(num_waypoints, list):
            if len(pd.Index(num_waypoints).difference(self.scdata.data.index)) > 0:
                warnings.warn(
                    "Some of the specified waypoints are not in the data. These will "
                    "be removed"
                )
                num_waypoints = list(self.scdata.data.index.intersection(num_waypoints))
        elif num_waypoints > self.scdata.data.shape[0]:
            raise RuntimeError(
                "num_waypoints parameter is higher than the number of "
                "cells in the dataset. \
                Please select a smaller number"
            )
        s = s[0]

        # Run the algorithm
        res = c_wishbone(
            self.scdata.latent_space.values,
            s=s,
            k=k,
            l=k,
            num_waypoints=num_waypoints,
            branch=branch,
        )

        # Assign results
        trajectory = res["Trajectory"]
        branches = res["Branches"]
        trajectory = (trajectory - np.min(trajectory)) / (
            np.max(trajectory) - np.min(trajectory)
        )
        self.trajectory = pd.Series(trajectory, index=self.scdata.data.index)
        self.branch = None
        if branch:
            self.branch = pd.Series(
                [int(i) for i in branches], index=self.scdata.data.index
            )
        self.waypoints = list(self.scdata.data.index[res["Waypoints"]])

        # Set branch colors
        if branch:
            self.branch_colors = dict(zip([2, 1, 3], qualitative_colors(3)))

       
    # Plotting functions
    # Function to plot wishbone results on tSNE
    def plot_wishbone_on_tsne(self):
        if self.trajectory is None:
            raise RuntimeError("Please run Wishbone run_wishbone before plotting")
        if self.scdata.tsne is None:
            raise RuntimeError("Please run tSNE using scdata.run_tsne before plotting")

        fig = plt.figure(figsize=[8, 4])
        gs = plt.GridSpec(1, 2)

        # Trajectory
        ax = plt.subplot(gs[0, 0])
        plt.scatter(
            self.scdata.tsne["x"],
            self.scdata.tsne["y"],
            edgecolors="none",
            s=10,
            cmap='viridis',
            c=self.trajectory,
        )
        ax.xaxis.set_major_locator(plt.NullLocator())
        ax.yaxis.set_major_locator(plt.NullLocator())
        plt.title("Wishbone trajectory")

        # Branch
        if self.branch is not None:
            ax = plt.subplot(gs[0, 1])
            plt.scatter(
                self.scdata.tsne["x"],
                self.scdata.tsne["y"],
                edgecolors="none",
                s=10,
                color=[self.branch_colors[i] for i in self.branch],
            )
            ax.xaxis.set_major_locator(plt.NullLocator())
            ax.yaxis.set_major_locator(plt.NullLocator())
            plt.title("Branch associations")

        return fig, ax
    
    def plot_wishbone_on_umap(self):
        """\
        Plot Wishbone results on UMAP maps
        """
        if self.trajectory is None:
            raise RuntimeError("Please run Wishbone using run_wishbone before plotting")
        if self.scdata.umap is None:
            raise RuntimeError("Please run UMAP using scdata.run_umap before plotting")

        # Set up figure
        fig = plt.figure(figsize=[8, 4])
        gs = plt.GridSpec(1, 2)

        # Trajectory
        ax = plt.subplot(gs[0, 0])
        plt.scatter(
            self.scdata.umap["x"],
            self.scdata.umap["y"],
            edgecolors="none",
            s=1,
            cmap='viridis',
            c=self.trajectory,
        )
        ax.xaxis.set_major_locator(plt.NullLocator())
        ax.yaxis.set_major_locator(plt.NullLocator())
        plt.title("Wishbone trajectory")

        # Branch
        if self.branch is not None:
            ax = plt.subplot(gs[0, 1])
            plt.scatter(
                self.scdata.umap["x"],
                self.scdata.umap["y"],
                edgecolors="none",
                s=1,
                color=[self.branch_colors[i] for i in self.branch],
            )
            ax.xaxis.set_major_locator(plt.NullLocator())
            ax.yaxis.set_major_locator(plt.NullLocator())
            plt.title("Branch associations")

        return fig, ax


    # Function to plot trajectory
def plot_marker_trajectory(
        self,
        markers: Iterable[str],
        show_variance: bool = False,
        no_bins: int = 150,
        smoothing_factor: int = 1,
        min_delta: float = 0.1,
        fig: matplotlib.figure.Figure = None,
        ax: matplotlib.axes.Axes = None,
    ) -> Tuple[dict, matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot marker trends along trajectory

        :param markers:
            Iterable of markers/genes to be plotted.
        :param show_variance:
            Logical indicating if the trends should be accompanied with variance
        :param no_bins:
            Number of bins for calculating marker density
        :param smoothing_factor:
            Parameter controlling the degree of smoothing
        :param min_delta:
            Minimum difference in marker expression after normalization to show
            separate trends for the two branches
        :param fig:
            matplotlib Figure object
        :param ax:
            matplotlib Axis object

        :return:
            Dictionary containing the determined trends for the different branches
        """
        if self.trajectory is None:
            raise RuntimeError("Please run Wishbone run_wishbone before plotting")
        # if self.scdata.data_type == 'sc-seq' and show_variance:
        #     raise RuntimeError('Variance calculation is currently not supported for
        #     single-cell RNA-seq')

        # Compute bin locations and bin memberships
        trajectory = self.trajectory.copy()
        # Sort trajectory
        trajectory = trajectory.sort_values()
        bins = np.linspace(np.min(trajectory), np.max(trajectory), no_bins)

        # Compute gaussian weights for points at each location
        # Standard deviation estimated from Silverman's approximation
        stdev = (
            np.std(trajectory) * 1.34 * len(trajectory) ** (-1 / 5) * smoothing_factor
        )
        weights = np.exp(
            -((np.tile(trajectory, [no_bins, 1]).T - bins) ** 2 / (2 * stdev ** 2))
        ) * (1 / (2 * np.pi * stdev ** 2) ** 0.5)

        # Adjust weights if data has branches
        if self.branch is not None:

            plot_branch = True

            # Branch of the trunk
            trunk = self.branch[trajectory.index[0]]
            branches = list(set(self.branch).difference([trunk]))
            linetypes = pd.Series([":", "--"], index=branches)

            # Counts of branch cells in each bin
            branch_counts = pd.DataFrame(
                np.zeros([len(bins) - 1, 3]), columns=[1, 2, 3]
            )
            for j in branch_counts.columns:
                branch_counts[j] = pd.Series(
                    [
                        sum(
                            self.branch[
                                trajectory.index[
                                    (trajectory > bins[i - 1]) & (trajectory < bins[i])
                                ]
                            ]
                            == j
                        )
                        for i in range(1, len(bins))
                    ]
                )
            # Frequencies
            branch_counts = branch_counts.divide(branch_counts.sum(axis=1), axis=0)

            # Identify the bin with the branch point by looking at the weights
            weights = pd.DataFrame(
                weights, index=trajectory.index, columns=range(no_bins)
            )
            bp_bin = weights.columns[np.where(branch_counts[trunk] < 0.9)[0][0]] + 0
            if bp_bin < 0:
                bp_bin = 3

        else:
            plot_branch = False
            bp_bin = no_bins

        weights_copy = weights.copy()

        # Plot marker tsne_res
        xaxis = bins

        # Set up return object
        ret_values = dict()
        ret_values["Trunk"] = pd.DataFrame(xaxis[0:bp_bin], columns=["x"])
        ret_values["Branch1"] = pd.DataFrame(xaxis[(bp_bin - 2) :], columns=["x"])
        ret_values["Branch2"] = pd.DataFrame(xaxis[(bp_bin - 2) :], columns=["x"])

        # Marker colors
        colors = qualitative_colors(len(markers))
        scaling_factor = 2
        linewidth = 3

        # Set up plot
        fig, ax = get_fig(fig, ax, figsize=[14, 4])

        for marker, color in zip(markers, colors):

            # Marker expression repeated no bins times
            y = self.scdata.data.loc[trajectory.index, marker]
            rep_mark = np.tile(y, [no_bins, 1]).T

            # Normalize y
            y_min = np.percentile(y, 1)
            y = (y - y_min) / (np.percentile(y, 99) - y_min)
            y[y < 0] = 0
            y[y > 1] = 1
            norm_rep_mark = pd.DataFrame(np.tile(y, [no_bins, 1])).T

            if not plot_branch:
                # Weight and plot
                vals = (rep_mark * weights) / sum(weights)

                # Normalize
                vals = vals.sum(axis=0)
                vals = vals - np.min(vals)
                vals = vals / np.max(vals)

                # Plot
                plt.plot(xaxis, vals, label=marker, color=color, linewidth=linewidth)

                # Show errors if specified
                if show_variance:
                    # Scale the marks based on y and values to be plotted
                    temp = ((norm_rep_mark - vals - np.min(y)) / np.max(y)) ** 2
                    # Calculate standard deviations
                    wstds = (
                        inner1d(np.asarray(temp).T, np.asarray(weights).T)
                        / weights.sum()
                    )

                    plt.fill_between(
                        xaxis,
                        vals - scaling_factor * wstds,
                        vals + scaling_factor * wstds,
                        alpha=0.2,
                        color=color,
                    )

                # Return values
                ret_values["Trunk"][marker] = vals[0:bp_bin]
                ret_values["Branch1"][marker] = vals[(bp_bin - 2) :]
                ret_values["Branch2"][marker] = vals[(bp_bin - 2) :]

            else:  # Branching trajectory
                rep_mark = pd.DataFrame(
                    rep_mark, index=trajectory.index, columns=range(no_bins)
                )

                plot_split = True
                # Plot trunk first
                weights = weights_copy.copy()

                plot_vals = ((rep_mark * weights) / np.sum(weights)).sum()
                trunk_vals = plot_vals[0:bp_bin]

                branch_vals = []
                for br in branches:
                    # Mute weights of the branch cells and plot
                    weights = weights_copy.copy()
                    weights.loc[self.branch.index[self.branch == br], :] = 0

                    plot_vals = ((rep_mark * weights) / np.sum(weights)).sum()
                    branch_vals.append(plot_vals[(bp_bin - 1) :])

                # Min and max
                temp = trunk_vals.append(branch_vals[0]).append(branch_vals[1])
                min_val = np.min(temp)
                max_val = np.max(temp)

                # Plot the trunk
                plot_vals = ((rep_mark * weights) / np.sum(weights)).sum()
                plot_vals = (plot_vals - min_val) / (max_val - min_val)
                plt.plot(
                    xaxis[0:bp_bin],
                    plot_vals[0:bp_bin],
                    label=marker,
                    color=color,
                    linewidth=linewidth,
                )

                if show_variance:
                    # Calculate weighted stds for plotting
                    # Scale the marks based on y and values to be plotted
                    temp = ((norm_rep_mark - plot_vals - np.min(y)) / np.max(y)) ** 2
                    # Calculate standard deviations
                    wstds = (
                        inner1d(np.asarray(temp).T, np.asarray(weights).T)
                        / weights.sum()
                    )

                    # Plot
                    plt.fill_between(
                        xaxis[0:bp_bin],
                        plot_vals[0:bp_bin] - scaling_factor * wstds[0:bp_bin],
                        plot_vals[0:bp_bin] + scaling_factor * wstds[0:bp_bin],
                        alpha=0.1,
                        color=color,
                    )

                # Add values to return values
                ret_values["Trunk"][marker] = plot_vals[0:bp_bin]

                # Identify markers which need a split
                if (
                    sum(
                        abs(pd.Series(branch_vals[0]) - pd.Series(branch_vals[1]))
                        > min_delta
                    )
                    < 5
                ):
                    # Split not necessary, plot the trunk values
                    plt.plot(
                        xaxis[(bp_bin - 1) :],
                        plot_vals[(bp_bin - 1) :],
                        color=color,
                        linewidth=linewidth,
                    )

                    # Add values to return values
                    ret_values["Branch1"][marker] = list(plot_vals[(bp_bin - 2) :])
                    ret_values["Branch2"][marker] = list(plot_vals[(bp_bin - 2) :])

                    if show_variance:
                        # Calculate weighted stds for plotting
                        # Scale the marks based on y and values to be plotted
                        temp = (
                            (norm_rep_mark - plot_vals - np.min(y)) / np.max(y)
                        ) ** 2
                        wstds = (
                            inner1d(np.asarray(temp).T, np.asarray(weights).T)
                            / weights.sum()
                        )
                        # Plot
                        plt.fill_between(
                            xaxis[(bp_bin - 1) :],
                            plot_vals[(bp_bin - 1) :]
                            - scaling_factor * wstds[(bp_bin - 1) :],
                            plot_vals[(bp_bin - 1) :]
                            + scaling_factor * wstds[(bp_bin - 1) :],
                            alpha=0.1,
                            color=color,
                        )
                else:
                    # Plot the two branches separately
                    for br_ind, br in enumerate(branches):
                        # Mute weights of the branch cells and plot
                        weights = weights_copy.copy()

                        # Smooth weigths
                        smooth_bins = 10
                        if bp_bin < smooth_bins:
                            smooth_bins = bp_bin - 1
                        for i in range(smooth_bins):
                            weights.loc[
                                self.branch == br, bp_bin + i - smooth_bins
                            ] *= ((smooth_bins - i) / smooth_bins) * 0.25
                        weights.loc[self.branch == br, (bp_bin) : weights.shape[1]] = 0

                        # Calculate values to be plotted
                        plot_vals = ((rep_mark * weights) / np.sum(weights)).sum()
                        plot_vals = (plot_vals - min_val) / (max_val - min_val)
                        plt.plot(
                            xaxis[(bp_bin - 2) :],
                            plot_vals[(bp_bin - 2) :],
                            linetypes[br],
                            color=color,
                            linewidth=linewidth,
                        )

                        if show_variance:
                            # Calculate weighted stds for plotting
                            # Scale the marks based on y and values to be plotted
                            temp = (
                                (norm_rep_mark - plot_vals - np.min(y)) / np.max(y)
                            ) ** 2
                            # Calculate standard deviations
                            wstds = (
                                inner1d(np.asarray(temp).T, np.asarray(weights).T)
                                / weights.sum()
                            )

                            # Plot
                            plt.fill_between(
                                xaxis[(bp_bin - 1) :],
                                plot_vals[(bp_bin - 1) :]
                                - scaling_factor * wstds[(bp_bin - 1) :],
                                plot_vals[(bp_bin - 1) :]
                                + scaling_factor * wstds[(bp_bin - 1) :],
                                alpha=0.1,
                                color=color,
                            )

                        # Add values to return values
                        ret_values["Branch%d" % (br_ind + 1)][marker] = list(
                            plot_vals[(bp_bin - 2) :]
                        )

        # Clean up the plotting
        # Clean xlim
        plt.legend(loc=2, bbox_to_anchor=(1, 1), prop={"size": 16})

        # Annotations
        # Add trajectory as underlay
        cm = matplotlib.cm.Spectral_r
        yval = plt.ylim()[0]
        yval = 0
        plt.scatter(
            trajectory,
            np.repeat(yval - 0.1, len(trajectory)),
            c=trajectory,
            cmap=cm,
            edgecolors="none",
            s=size,
        )
        sns.despine()
        plt.xticks(np.arange(0, 1.1, 0.1))

        # Clean xlim
        plt.xlim([-0.05, 1.05])
        plt.ylim([-0.2, 1.1])
        plt.xlabel("Wishbone trajectory")
        plt.ylabel("Normalized expression")

        return ret_values, fig, ax

def plot_marker_heatmap(
        self, marker_trends, trajectory_range: list = [0, 1]
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot expression of markers as a heatmap

        :param marker_trends:
            Output from the plot_marker_trajectory function
        :param trajectory_range:
            Range of the trajectory in which to plot the results
        """
        if trajectory_range[0] >= trajectory_range[1]:
            raise RuntimeError("Start cannot exceed end in trajectory_range")
        if trajectory_range[0] < 0 or trajectory_range[1] > 1:
            raise RuntimeError("Please use a range between (0, 1)")

        # Set up figure
        markers = marker_trends["Trunk"].columns[1:]

        if self.branch is not None:
            fig = plt.figure(figsize=[16, 0.5 * len(markers)])
            gs = plt.GridSpec(1, 2)

            branches = np.sort(list(set(marker_trends.keys()).difference(["Trunk"])))
            for i, br in enumerate(branches):
                ax = plt.subplot(gs[0, i])

                # Construct the full matrix
                mat = marker_trends["Trunk"].append(marker_trends[br][2:])
                mat.index = range(mat.shape[0])

                # Start and end
                start = np.where(mat["x"] >= trajectory_range[0])[0][0]
                end = np.where(mat["x"] >= trajectory_range[1])[0][0]

                # Plot
                plot_mat = mat.loc[start:end]
                sns.heatmap(
                    plot_mat[markers].T, linecolor="none", cmap=cmap, vmin=0, vmax=1
                )
                ax.xaxis.set_major_locator(plt.NullLocator())
                ticks = np.arange(trajectory_range[0], trajectory_range[1] + 0.1, 0.1)
                plt.xticks([np.where(plot_mat["x"] >= i)[0][0] for i in ticks], ticks)

                # Labels
                plt.xlabel("Wishbone trajectory")
                plt.title(br)
        else:
            # Plot values from the trunk alone
            fig = plt.figure(figsize=[8, 0.5 * len(markers)])
            ax = plt.gca()

            # Construct the full matrix
            mat = marker_trends["Trunk"]
            mat.index = range(mat.shape[0])

            # Start and end
            start = np.where(mat["x"] >= trajectory_range[0])[0][0]
            end = np.where(mat["x"] >= trajectory_range[1])[0][0]

            # Plot
            plot_mat = mat.loc[start:end]
            sns.heatmap(
                plot_mat[markers].T, linecolor="none", cmap=cmap, vmin=0, vmax=1
            )
            ax.xaxis.set_major_locator(plt.NullLocator())
            ticks = np.arange(trajectory_range[0], trajectory_range[1] + 0.1, 0.1)
            plt.xticks([np.where(plot_mat["x"] >= i)[0][0] for i in ticks], ticks)

            # Labels
            plt.xlabel("Wishbone trajectory")

        return fig, ax

def plot_derivatives(
        self, marker_trends, trajectory_range: list = [0, 1]
    ) -> Tuple[matplotlib.figure.Figure, matplotlib.axes.Axes]:
        """\
        Plot change in expression of markers along trajectory

        :param marker_trends:
            Output from the plot_marker_trajectory function
        :param trajectory_range:
            Range of the trajectory in which to plot the results
        """
        if trajectory_range[0] >= trajectory_range[1]:
            raise RuntimeError("Start cannot exceed end in trajectory_range")
        if trajectory_range[0] < 0 or trajectory_range[1] > 1:
            raise RuntimeError("Please use a range between (0, 1)")

        # Set up figure
        markers = marker_trends["Trunk"].columns[1:]

        if self.branch is not None:
            fig = plt.figure(figsize=[16, 0.5 * len(markers)])
            gs = plt.GridSpec(1, 2)

            branches = np.sort(list(set(marker_trends.keys()).difference(["Trunk"])))
            for i, br in enumerate(branches):
                ax = plt.subplot(gs[0, i])

                # Construct the full matrix
                mat = marker_trends["Trunk"].append(marker_trends[br][2:])
                mat.index = range(mat.shape[0])

                # Start and end
                start = np.where(mat["x"] >= trajectory_range[0])[0][0]
                end = np.where(mat["x"] >= trajectory_range[1])[0][0]

                # Plot
                diffs = mat[markers].diff()
                diffs[diffs.isnull()] = 0

                # Update the branch points diffs
                bp_bin = marker_trends["Trunk"].shape[0]
                diffs.loc[bp_bin - 1] = (
                    marker_trends[br].loc[0:1, markers].diff().loc[1]
                )
                diffs.loc[bp_bin] = marker_trends[br].loc[1:2, markers].diff().loc[2]
                diffs = diffs.loc[start:end]
                mat = mat.loc[start:end]

                # Differences
                vmax = max(0.05, abs(diffs).max().max())
                # Plot
                sns.heatmap(
                    diffs.T,
                    linecolor="none",
                    cmap=matplotlib.cm.RdBu_r,
                    vmin=-vmax,
                    vmax=vmax,
                )
                ax.xaxis.set_major_locator(plt.NullLocator())
                ticks = np.arange(trajectory_range[0], trajectory_range[1] + 0.1, 0.1)
                plt.xticks([np.where(mat["x"] >= i)[0][0] for i in ticks], ticks)

                # Labels
                plt.xlabel("Wishbone trajectory")
                plt.title(br)
        else:
            # Plot values from the trunk alone
            fig = plt.figure(figsize=[8, 0.5 * len(markers)])
            ax = plt.gca()

            # Construct the full matrix
            mat = marker_trends["Trunk"]
            mat.index = range(mat.shape[0])

            # Start and end
            start = np.where(mat["x"] >= trajectory_range[0])[0][0]
            end = np.where(mat["x"] >= trajectory_range[1])[0][0]

            # Plot
            diffs = mat[markers].diff()
            diffs[diffs.isnull()] = 0
            diffs = diffs.loc[start:end]
            mat = mat.loc[start:end]

            # Differences
            vmax = max(0.05, abs(diffs).max().max())
            # Plot
            sns.heatmap(
                diffs.T,
                linecolor="none",
                cmap=matplotlib.cm.RdBu_r,
                vmin=-vmax,
                vmax=vmax,
            )
            ax.xaxis.set_major_locator(plt.NullLocator())
            ticks = np.arange(trajectory_range[0], trajectory_range[1] + 0.1, 0.1)
            plt.xticks([np.where(mat["x"] >= i)[0][0] for i in ticks], ticks)

            # Labels
            plt.xlabel("Wishbone trajectory")

        return fig, ax
