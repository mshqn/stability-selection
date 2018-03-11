"""
===============================
Stability selection transformer
===============================

This module contains a scikit-learn compatible implementation of stability selection[1].

References
----------
[1] Meinshausen, N. and Buhlmann, P., 2010. Stability selection. Journal of the Royal Statistical Society:
    Series B (Statistical Methodology), 72(4), pp.417-473.

"""

import numpy as np
import matplotlib.pyplot as plt

from sklearn.base import BaseEstimator, TransformerMixin, clone
from sklearn.externals.joblib import Parallel, delayed
from sklearn.feature_selection import SelectFromModel
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.utils import check_X_y, check_array, safe_mask, check_random_state
from sklearn.utils.random import sample_without_replacement
from sklearn.utils.validation import check_is_fitted
from warnings import warn


__all__ = ['StabilitySelection', 'plot_stability_path']


def _return_estimator_from_pipeline(pipeline):
    """Returns the final estimator in a Pipeline, or the estimator if it is not"""
    if isinstance(pipeline, Pipeline):
        return pipeline._final_estimator
    else:
        return pipeline


def _fit_bootstrap_sample(base_estimator, X, y, lambda_name, lambda_value, threshold=None, random_state=None):
    """
    Fits base_estimator on a bootstrap sample of the original data, and returns a mas of the variables that are \
    selected by the fitted model.

    Parameters
    ----------
    base_estimator : Estimator
        Estimator to be fitted on the data

    X : {array-like, sparse matrix}, shape = [n_samples, n_features]
        The training input samples.

    y : array-like, shape = [n_samples]
        The target values.

    lambda_name : str
        Name of the penalization parameter of base_estimator

    lambda_value : float
        Value of the penalization parameter

    Returns
    -------
    selected_variables : array-like, shape = [n_features]
        Boolean mask of selected variables.
    """
    n_samples = X.shape[0]
    bootstrap = sample_without_replacement(n_samples, n_samples // 2, random_state=random_state)
    X_train, y_train = X[safe_mask(X, bootstrap), :], y[bootstrap]

    base_estimator.set_params(**{lambda_name: lambda_value})
    base_estimator.fit(X_train, y_train)

    # TODO: Reconsider if we really want to use SelectFromModel here or not
    selector_model = _return_estimator_from_pipeline(base_estimator)
    variable_selector = SelectFromModel(estimator=selector_model, threshold=threshold, prefit=True)
    return variable_selector.get_support()


def plot_stability_path(stability_selection, threshold_highlight=None, **kwargs):
    """Plots stability path.

    Parameters
    ----------
    stability_selection : StabilitySelection
        Fitted instance of StabilitySelection.

    threshold_highlight : float
        Threshold defining the cutoff for the stability scores for the variables that need to be highlighted.

    kwargs : dict
        Arguments passed to matplotlib plot function.
    """
    check_is_fitted(stability_selection, 'stability_scores_')

    threshold = stability_selection.threshold if threshold_highlight is None else threshold_highlight
    paths_to_highlight = stability_selection.get_support(threshold=threshold)

    fig, ax = plt.subplots(1, 1, **kwargs)
    ax.plot(stability_selection.lambda_grid,
            stability_selection.stability_scores_[~paths_to_highlight].T, 'b-')
    ax.plot(stability_selection.lambda_grid,
            stability_selection.stability_scores_[paths_to_highlight].T, 'r-')

    if threshold is not None:
        ax.plot(stability_selection.lambda_grid,
                threshold * np.ones_like(stability_selection.lambda_grid), '--')

    ax.set_ylabel('Stability score')
    ax.set_xlabel('Lambda')

    fig.tight_layout()

    return fig, ax


class StabilitySelection(BaseEstimator, TransformerMixin):
    """Stability selection fits the estimator `base_estimator` on bootstrap samples of the original data set, for
    different values of the regularization parameter for `base_estimator`. Variables that reliably get selected by the
    model in these bootstrap samples are considered to be stable variables.

    Parameters
    ----------
    base_estimator : object.
        The base estimator used for stability selection.

    lambda_name : str.
        The name of the penalizatin parameter for the estimator `base_estimator`.

    lambda_grid : array-like.
        Grid of values of the penalization parameter to iterate over.

    n_bootstrap_iterations : integer.
        Number of bootstrap samples to create.

    threshold: float.
        Threshold defining the minimum cutoff value for the stability scores.

    bootstrap_threshold: string, float, optional default None
        The threshold value to use for feature selection. Features whose
        importance is greater or equal are kept while the others are
        discarded. If "median" (resp. "mean"), then the ``threshold`` value is
        the median (resp. the mean) of the feature importances. A scaling
        factor (e.g., "1.25*mean") may also be used. If None and if the
        estimator has a parameter penalty set to l1, either explicitly
        or implicitly (e.g, Lasso), the threshold used is 1e-5.
        Otherwise, "mean" is used by default.

    verbose : integer.
        Controls the verbosity: the higher, the more messages.

    n_jobs : int, default=1
        Number of jobs to run in parallel.

    pre_dispatch : int, or string, optional
        Controls the number of jobs that get dispatched during parallel
        execution. Reducing this number can be useful to avoid an
        explosion of memory consumption when more jobs get dispatched
        than CPUs can process. This parameter can be:
            - None, in which case all the jobs are immediately
              created and spawned. Use this for lightweight and
              fast-running jobs, to avoid delays due to on-demand
              spawning of the jobs
            - An int, giving the exact number of total jobs that are
              spawned
            - A string, giving an expression as a function of n_jobs,
              as in '2*n_jobs'

    random_state : int, RandomState instance or None, optional, default=None
        Pseudo random number generator state used for random uniform sampling
        from lists of possible values instead of scipy.stats distributions.
        If int, random_state is the seed used by the random number generator;
        If RandomState instance, random_state is the random number generator;
        If None, the random number generator is the RandomState instance used
        by `np.random`.

    Attributes
    ----------
    stability_scores_ : array, shape = [n_features, n_alphas]
        Array of stability scores for each feature for each value of the penalization parameter.
    """
    def __init__(self, base_estimator=LogisticRegression(penalty='l1'), lambda_name='C', lambda_grid=None,
                 n_bootstrap_iterations=100, threshold=0.6,
                 bootstrap_threshold=None, verbose=0, n_jobs=1, pre_dispatch='2*n_jobs', random_state=None):
        self.base_estimator = base_estimator
        self.lambda_name = lambda_name
        self.lambda_grid = lambda_grid
        self.n_bootstrap_iterations = n_bootstrap_iterations
        self.threshold = threshold
        self.bootstrap_threshold = bootstrap_threshold
        self.verbose = verbose
        self.n_jobs = n_jobs
        self.pre_dispatch = pre_dispatch
        self.random_state = random_state

    def _validate_input(self):
        if not isinstance(self.n_bootstrap_iterations, int) or self.n_bootstrap_iterations <= 0:
            raise ValueError('n_bootstrap_iterations should be a positive integer, got %s' % self.n_bootstrap_iterations)

        if not isinstance(self.threshold, float) or not (0.0 < self.threshold < 1.0):
            raise ValueError('threshold should be a float in (0.0, 1.0], got %s' % self.threshold)

        if self.lambda_name not in self.base_estimator.get_params().keys():
            raise ValueError('lambda_name is set to %s, but base_estimator %s does not have a parameter '
                             'with that name' % (self.lambda_name, self.base_estimator.__class__.__name__))

        if self.lambda_grid is None:
            self.lambda_grid = np.logspace(-5, -2, 25)

    def fit(self, X, y):
        """Fit the stability selection model on the given data.

        Parameters
        ----------
        X : {array-like, sparse matrix}, shape = [n_samples, n_features]
            The training input samples.

        y : array-like, shape = [n_samples]
            The target values.
        """

        self._validate_input()

        X, y = check_X_y(X, y)
        n_samples, n_variables = X.shape
        n_lambdas = self.lambda_grid.shape[0]

        base_estimator = clone(self.base_estimator)
        random_state = check_random_state(self.random_state)
        stability_scores = np.zeros((n_variables, n_lambdas))

        for idx, lambda_value in enumerate(self.lambda_grid):
            if self.verbose > 0:
                print("Fitting estimator for lambda = %.5f (%d / %d) on %d bootstrap samples" %
                      (lambda_value, idx + 1, n_lambdas, self.n_bootstrap_iterations))

            selected_variables = Parallel(
                n_jobs=self.n_jobs, verbose=self.verbose,
                pre_dispatch=self.pre_dispatch
            )(delayed(_fit_bootstrap_sample)(clone(base_estimator), X, y, self.lambda_name, lambda_value,
                                             threshold=self.bootstrap_threshold, random_state=random_state)
              for _ in range(self.n_bootstrap_iterations))

            stability_scores[:, idx] = np.vstack(selected_variables).mean(axis=0)

        self.stability_scores_ = stability_scores
        return self

    def get_support(self, indices=False, threshold=None):
        """Get a mask, or integer index, of the features selected

        Parameters
        ----------
        indices : boolean (default False)
            If True, the return value will be an array of integers, rather
            than a boolean mask.

        threshold: float.
            Threshold defining the minimum cutoff value for the stability scores.

        Returns
        -------
        support : array
            An index that selects the retained features from a feature vector.
            If `indices` is False, this is a boolean array of shape
            [# input features], in which an element is True iff its
            corresponding feature is selected for retention. If `indices` is
            True, this is an integer array of shape [# output features] whose
            values are indices into the input feature vector.
        """

        if threshold is not None and not isinstance(threshold, float):
            raise ValueError('threshold should be a float in (0.0, 1.0], got %s' % self.threshold)

        if threshold is not None and (threshold < 0.0 or threshold > 1.0):
            raise ValueError('threshold should be a float in (0.0, 1.0], got %s' % self.threshold)

        cutoff = self.threshold if threshold is None else threshold
        mask = (self.stability_scores_.max(axis=1) > cutoff)

        return mask if not indices else np.where(mask)[0]

    def transform(self, X, threshold=None):
        """Reduce X to the selected features.

        Parameters
        ----------
        X : array of shape [n_samples, n_features]
            The input samples.

        threshold: float.
            Threshold defining the minimum cutoff value for the stability scores.

        Returns
        -------
        X_r : array of shape [n_samples, n_selected_features]
            The input samples with only the selected features.
        """
        X = check_array(X)
        mask = self.get_support(threshold=threshold)

        check_is_fitted(self, 'stability_scores_')

        if not mask.any():
            warn("No features were selected: either the data is"
                 " too noisy or the selection test too strict.",
                 UserWarning)
            return np.empty(0).reshape((X.shape[0], 0))
        if len(mask) != X.shape[1]:
            raise ValueError("X has a different shape than during fitting.")
        return X[:, safe_mask(X, mask)]
